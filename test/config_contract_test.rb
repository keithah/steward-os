# frozen_string_literal: true

require 'minitest/autorun'
require 'tmpdir'
require 'English'
require_relative '../lib/config_contract'

FIXTURE_ROOT = File.expand_path('fixtures/config', __dir__)

class ExtractKeyPathsTest < Minitest::Test
  def paths(node)
    ConfigContract::Extract.key_paths(node)
  end

  def test_nested_hash_becomes_dotted_paths
    assert_equal %w[a.b.c], paths({ 'a' => { 'b' => { 'c' => 1 } } })
  end

  def test_list_elements_normalize_to_brackets
    node = { 'repos' => [{ 'name' => 'x' }, { 'name' => 'y' }] }
    assert_equal %w[repos[].name], paths(node).uniq
  end

  def test_empty_collection_is_itself_a_leaf
    assert_equal %w[a], paths({ 'a' => [] })
    assert_equal %w[b], paths({ 'b' => {} })
  end

  def test_walk_stops_at_opaque_subtrees
    node = { 'labels' => { 'area' => { 'map' => { 'skills/**' => 'area:skills' } } } }
    assert_equal %w[labels.area.map], paths(node)
  end

  def test_opaque_list_stays_shallow_for_size_buckets
    node = { 'labels' => { 'size' => { 'buckets' => { 'S' => 50, 'XL' => nil } } } }
    assert_equal %w[labels.size.buckets], paths(node)
  end
end

class ExtractEmptyCollectionPathsTest < Minitest::Test
  def empties(node)
    ConfigContract::Extract.empty_collection_paths(node)
  end

  def test_finds_empty_hash_and_empty_array_leaves_only
    node = { 'a' => [], 'b' => {}, 'c' => 1, 'd' => { 'e' => [] } }
    assert_equal %w[a b d.e].sort, empties(node).sort
  end
end

class ExtractLoadTest < Minitest::Test
  def test_load_returns_parsed_hash
    Dir.mktmpdir do |dir|
      path = File.join(dir, 'c.yaml')
      File.write(path, "a:\n  b: 1\n")
      assert_equal({ 'a' => { 'b' => 1 } }, ConfigContract::Extract.load(path))
    end
  end

  def test_load_raises_a_clear_message_on_malformed_yaml
    Dir.mktmpdir do |dir|
      path = File.join(dir, 'bad.yaml')
      File.write(path, "a:\n  - [unclosed\n")
      err = assert_raises(RuntimeError) { ConfigContract::Extract.load(path) }
      assert_match(/could not parse/, err.message)
      assert_match(/bad\.yaml/, err.message)
    end
  end

  def test_load_of_empty_file_returns_empty_hash
    Dir.mktmpdir do |dir|
      path = File.join(dir, 'empty.yaml')
      File.write(path, '')
      assert_equal({}, ConfigContract::Extract.load(path))
    end
  end
end

# --- Phase 1: shape ---
class ShapeTest < Minitest::Test
  TEMPLATE = {
    'project' => { 'name' => '', 'repo' => '' },
    'issue_capture' => { 'enabled' => false, 'sources' => [] },
    'scheduled_jobs' => {
      'jobs' => [
        { 'name' => 'a', 'every' => '1h', 'enabled' => false },
        { 'name' => 'b', 'cron' => '30 8 * * *', 'enabled' => false }
      ]
    }
  }.freeze

  def rules(violations)
    violations.map(&:rule)
  end

  def check(config)
    ConfigContract.check(config: config, template: TEMPLATE)
  end

  def test_matching_shape_reports_no_shape_violations
    refute_includes rules(check(TEMPLATE)), :unknown_key
    refute_includes rules(check(TEMPLATE)), :missing_key
  end

  def test_unknown_key_is_an_error
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['issue_captures'] = { 'enabled' => true }
    v = check(cfg).find { |x| x.rule == :unknown_key }
    assert_equal :error, v.severity
    assert_equal 'issue_captures.enabled', v.key
  end

  def test_missing_key_is_an_error
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['project'].delete('name')
    v = check(cfg).find { |x| x.rule == :missing_key }
    assert_equal :error, v.severity
    assert_equal 'project.name', v.key
  end

  def test_unknown_key_where_the_template_nests_a_mapping_says_so
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['issue_capture'] = 'skip'
    v = check(cfg).find { |x| x.rule == :unknown_key }
    assert_equal :error, v.severity
    assert_equal 'issue_capture', v.key
    assert_match(/nests keys under this one — expected a mapping, not a scalar/, v.message)
  end

  def test_populated_list_satisfies_an_empty_template_list
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['issue_capture']['sources'] = %w[email webform]
    assert_empty check(cfg).select { |x| %i[unknown_key missing_key].include?(x.rule) }
  end

  def test_job_with_neither_every_nor_cron_is_an_error
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['scheduled_jobs']['jobs'][0].delete('every')
    v = check(cfg).find { |x| x.rule == :job_schedule_alternation }
    assert_equal :error, v.severity
    assert_match(/exactly one/, v.message)
  end

  def test_job_with_both_every_and_cron_is_an_error
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['scheduled_jobs']['jobs'][0]['cron'] = '0 * * * *'
    assert_includes rules(check(cfg)), :job_schedule_alternation
  end

  def test_missing_cron_on_a_job_is_not_a_missing_key
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['scheduled_jobs']['jobs'] = [{ 'name' => 'a', 'every' => '1h', 'enabled' => false }]
    assert_empty check(cfg).select { |x| x.rule == :missing_key }
  end

  def test_empty_config_list_satisfies_a_populated_template_list
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['scheduled_jobs']['jobs'] = []
    assert_empty check(cfg).select { |x| %i[unknown_key missing_key].include?(x.rule) }
  end

  def test_empty_config_hash_satisfies_a_populated_template_subtree
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['scheduled_jobs'] = {}
    assert_empty check(cfg).select { |x| %i[unknown_key missing_key].include?(x.rule) }
  end

  def test_nil_ancestor_does_not_suppress_missing_keys
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['scheduled_jobs'] = nil
    assert_includes rules(check(cfg)), :missing_key,
                    'a nil ancestor is not an empty collection — suppressing here would be fail-open'
  end

  def test_scalar_ancestor_does_not_suppress_missing_keys
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['scheduled_jobs'] = 'yes'
    assert_includes rules(check(cfg)), :missing_key
  end

  def test_non_mapping_document_is_an_error
    v = ConfigContract.check(config: [], template: TEMPLATE)
    assert_equal :error, v.find { |x| x.rule == :not_a_mapping }.severity
    assert_includes v.map(&:rule), :phases_skipped,
                    'a suppressing error must always announce the suppression'
  end

  def test_shape_error_suppresses_later_phases
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['bogus'] = 1
    v = check(cfg)
    assert_includes rules(v), :unknown_key
    assert_includes rules(v), :phases_skipped
  end
end

# --- Phase 2: types ---
class TypeTest < Minitest::Test
  def base
    {
      'repositories' => [{ 'visibility' => 'public' }],
      'community' => { 'chat' => { 'monitor' => false, 'platform' => '', 'capture_reaction' => '' } },
      'issue_capture' => {
        'enabled' => false, 'capture_marker' => '', 'queue_path' => '',
        'ledger_path' => '', 'checkpoint_path' => '',
        'max_per_run' => 10, 'max_public_files_per_run' => 0
      },
      'security' => { 'security_contact' => '' },
      'secrets' => { 'execute_contributor_code' => false, 'sandbox_available' => false },
      'scheduled_jobs' => {
        'jobs' => [{ 'name' => 'action-watchdog', 'every' => '6h', 'enabled' => false }],
        'outputs' => { 'index_path' => '', 'digest_to' => '', 'alarms_to' => '' }
      }
    }
  end

  def check(config)
    ConfigContract.check(config: config, template: base)
  end

  def find(config, rule)
    check(config).find { |x| x.rule == rule }
  end

  def test_string_false_is_not_a_boolean
    cfg = base
    cfg['issue_capture']['enabled'] = 'false'
    v = find(cfg, :type_boolean)
    assert_equal :error, v.severity
    assert_equal 'issue_capture.enabled', v.key
    assert_match(/String/, v.message)
  end

  def test_real_boolean_passes
    assert_nil find(base, :type_boolean)
  end

  def test_integer_where_a_string_belongs_is_an_error
    cfg = base
    cfg['scheduled_jobs']['outputs']['alarms_to'] = 42
    assert_equal 'scheduled_jobs.outputs.alarms_to', find(cfg, :type_string).key
  end

  def test_blank_string_is_a_valid_string
    assert_nil find(base, :type_string)
  end

  def test_negative_bound_is_an_error
    cfg = base
    cfg['issue_capture']['max_per_run'] = -1
    assert_equal 'issue_capture.max_per_run', find(cfg, :type_non_negative_integer).key
  end

  def test_misspelled_visibility_is_an_error
    cfg = base
    cfg['repositories'][0]['visibility'] = 'publik'
    v = find(cfg, :type_enum)
    assert_equal 'repositories[0].visibility', v.key
    assert_match(/public/, v.message)
  end

  def test_non_boolean_job_enabled_is_an_error
    cfg = base
    cfg['scheduled_jobs']['jobs'][0]['enabled'] = 'yes'
    assert_equal 'scheduled_jobs.jobs[0].enabled', find(cfg, :type_boolean).key
  end

  def test_type_error_suppresses_later_phases
    cfg = base
    cfg['issue_capture']['enabled'] = 'false'
    assert_includes check(cfg).map(&:rule), :phases_skipped
  end

  def test_non_hash_list_elements_are_skipped_not_crashed
    cfg = base
    cfg['repositories'] << 'not-a-hash'
    cfg['scheduled_jobs']['jobs'] << 'also-not-a-hash'

    assert_equal [], ConfigContract.repository_violations(cfg)
    assert_equal [], ConfigContract.job_schedule_violations(cfg)
    assert_equal [], ConfigContract.job_type_violations(cfg)
  end
end

# --- Phase 3: semantic invariants ---
class SemanticTest < Minitest::Test
  # A minimal, fully-enabled, correctly-configured config. Every semantic rule
  # is satisfied here, so each test below breaks exactly one thing.
  def base
    {
      'repositories' => [{ 'visibility' => 'private' }],
      'community' => { 'chat' => { 'monitor' => true, 'platform' => 'discord',
                                   'capture_reaction' => 'white_check_mark' } },
      'issue_capture' => {
        'enabled' => true, 'capture_marker' => 'steward-captured',
        'queue_path' => '/srv/steward/queue.md', 'ledger_path' => '/srv/steward/ledger.md',
        'checkpoint_path' => '/srv/steward/checkpoint.md',
        'max_per_run' => 10, 'max_public_files_per_run' => 0
      },
      'security' => { 'security_contact' => 'github-advisory' },
      'secrets' => { 'execute_contributor_code' => false, 'sandbox_available' => false },
      'scheduled_jobs' => {
        'jobs' => [{ 'name' => 'action-watchdog', 'every' => '6h', 'enabled' => true }],
        'outputs' => { 'index_path' => '/srv/steward/index.md', 'digest_to' => '',
                       'alarms_to' => 'ops@example.com' }
      }
    }
  end

  def check(config)
    ConfigContract.check(config: config, template: base)
  end

  def rule(config, sym)
    check(config).find { |x| x.rule == sym }
  end

  def errors(config)
    check(config).select { |x| x.severity == :error }
  end

  def test_the_fully_enabled_base_has_no_errors
    assert_empty errors(base).map(&:rule)
  end

  def test_enabled_capture_without_alarms_to_is_an_error
    cfg = base
    cfg['scheduled_jobs']['outputs']['alarms_to'] = ''
    assert_equal :error, rule(cfg, :alarms_to_required).severity
  end

  def test_whitespace_only_alarms_to_counts_as_blank
    cfg = base
    cfg['scheduled_jobs']['outputs']['alarms_to'] = '   '
    assert_equal :error, rule(cfg, :alarms_to_required).severity
  end

  def test_disabled_capture_needs_no_alarms_to
    cfg = base
    cfg['issue_capture']['enabled'] = false
    cfg['community']['chat']['monitor'] = false
    cfg['issue_capture']['capture_marker'] = ''
    cfg['scheduled_jobs']['outputs']['alarms_to'] = ''
    assert_nil rule(cfg, :alarms_to_required)
  end

  def test_enabled_capture_without_state_paths_is_an_error
    cfg = base
    cfg['issue_capture']['ledger_path'] = ''
    v = rule(cfg, :capture_state_paths_required)
    assert_equal :error, v.severity
    assert_match(/ledger_path/, v.message)
  end

  def test_repo_relative_state_path_in_a_public_repo_is_an_error
    cfg = base
    cfg['repositories'] = [{ 'visibility' => 'public' }]
    cfg['issue_capture']['ledger_path'] = 'state/ledger.md'
    v = rule(cfg, :state_paths_not_public)
    assert_equal :error, v.severity
    assert_match(/ledger_path/, v.message)
  end

  def test_enabled_capture_without_pull_index_is_an_error
    cfg = base
    cfg['scheduled_jobs']['outputs']['index_path'] = ''
    assert_equal :error, rule(cfg, :pull_index_required).severity
  end

  def test_repo_relative_index_in_a_public_repo_is_an_error
    cfg = base
    cfg['repositories'] = [{ 'visibility' => 'public' }]
    cfg['scheduled_jobs']['outputs']['index_path'] = 'state/index.md'
    assert_equal :error, rule(cfg, :index_not_public).severity
  end

  def test_mixed_visibility_still_errors_fail_closed
    cfg = base
    cfg['repositories'] = [{ 'visibility' => 'private' }, { 'visibility' => 'public' }]
    cfg['scheduled_jobs']['outputs']['index_path'] = 'state/index.md'
    assert_equal :error, rule(cfg, :index_not_public).severity
  end

  def test_no_declared_repositories_is_unknown_visibility_fail_closed
    cfg = base
    cfg['repositories'] = []
    cfg['scheduled_jobs']['outputs']['index_path'] = 'state/index.md'
    assert_equal :error, rule(cfg, :index_not_public).severity
  end

  def test_repo_relative_index_in_a_private_repo_is_fine
    cfg = base
    cfg['scheduled_jobs']['outputs']['index_path'] = 'state/index.md'
    assert_nil rule(cfg, :index_not_public)
  end

  def test_repo_relative_state_path_in_a_private_repo_is_fine
    cfg = base
    cfg['issue_capture']['ledger_path'] = 'state/ledger.md'
    assert_nil rule(cfg, :state_paths_not_public)
  end

  def test_public_repo_index_with_capture_disabled_is_fine
    cfg = base
    cfg['repositories'] = [{ 'visibility' => 'public' }]
    cfg['issue_capture']['enabled'] = false
    cfg['community']['chat']['monitor'] = false
    cfg['issue_capture']['capture_marker'] = ''
    cfg['scheduled_jobs']['outputs']['index_path'] = 'state/index.md'
    assert_nil rule(cfg, :index_not_public)
  end

  def test_marker_without_enabled_watchdog_is_an_error
    cfg = base
    cfg['scheduled_jobs']['jobs'][0]['enabled'] = false
    assert_equal :error, rule(cfg, :marker_needs_watchdog).severity
  end

  def test_marker_with_no_watchdog_job_at_all_is_an_error
    cfg = base
    cfg['scheduled_jobs']['jobs'] = [{ 'name' => 'label-sync', 'every' => '4h', 'enabled' => true }]
    assert_equal :error, rule(cfg, :marker_needs_watchdog).severity
  end

  def test_reaction_with_monitor_and_no_watchdog_is_an_error
    cfg = base
    cfg['issue_capture']['capture_marker'] = ''
    cfg['scheduled_jobs']['jobs'][0]['enabled'] = false
    assert_equal :error, rule(cfg, :reaction_needs_watchdog).severity
  end

  def test_monitor_without_capture_core_is_an_error
    cfg = base
    cfg['issue_capture']['enabled'] = false
    assert_equal :error, rule(cfg, :chat_needs_capture_core).severity
  end

  def test_monitor_without_platform_is_an_error
    cfg = base
    cfg['community']['chat']['platform'] = ''
    assert_equal :error, rule(cfg, :chat_needs_platform).severity
    assert_nil rule(cfg, :phases_skipped),
              'phase 3 ran and found this fault — it must not also claim it never ran'
  end

  def test_public_filing_without_watchdog_is_an_error
    cfg = base
    cfg['issue_capture']['capture_marker'] = ''
    cfg['community']['chat']['capture_reaction'] = ''
    cfg['issue_capture']['max_public_files_per_run'] = 3
    cfg['scheduled_jobs']['jobs'][0]['enabled'] = false
    assert_equal :error, rule(cfg, :public_files_need_watchdog).severity
  end

  def test_contributor_code_without_sandbox_is_an_error
    cfg = base
    cfg['secrets']['execute_contributor_code'] = true
    assert_equal :error, rule(cfg, :sandbox_required).severity
  end

  def test_undecidable_destinations_warn_once_and_do_not_error
    cfg = base
    cfg['scheduled_jobs']['outputs']['digest_to'] = '#steward-digest'
    v = check(cfg)
    warnings = v.select { |x| x.rule == :destinations_unverifiable }
    assert_equal 1, warnings.size
    assert_equal :warning, warnings.first.severity
    assert_match(/index_path/, warnings.first.message)
    assert_match(/digest_to/, warnings.first.message)
    assert_empty v.select { |x| x.severity == :error }
  end

  def test_no_unverifiable_warning_when_capture_is_disabled
    cfg = base
    cfg['issue_capture']['enabled'] = false
    cfg['community']['chat']['monitor'] = false
    cfg['issue_capture']['capture_marker'] = ''
    assert_nil rule(cfg, :destinations_unverifiable)
  end

  # The whole design rests on phase 1/2 errors suppressing phase 3. Prove it
  # directly: a config that would trip index_not_public AND carries a type
  # error must report the type error and phases_skipped, never the semantic
  # rule the type error masked.
  def test_a_type_error_suppresses_a_semantic_rule_that_would_otherwise_fire
    cfg = base
    cfg['repositories'] = [{ 'visibility' => 'public' }]
    cfg['scheduled_jobs']['outputs']['index_path'] = 'state/index.md'
    cfg['secrets']['execute_contributor_code'] = 'yes'

    rules = check(cfg).map(&:rule)
    assert_includes rules, :type_boolean
    assert_includes rules, :phases_skipped
    refute_includes rules, :index_not_public
  end

  # --- Safe escape hatches ---
  #
  # Four rules fire on `precondition && !remedy`. The reds prove they fire when
  # the remedy is absent; the green never reaches the precondition at all. So
  # nothing otherwise pins the corner where the precondition IS met and the
  # adopter HAS applied the remedy the message tells them to apply.
  #
  # The two privacy rules (index_not_public, state_paths_not_public) get one
  # test per conjunct, not one test total: the absolute-path test pins
  # repo_relative?, the private-repo test pins public_repo?. A single test
  # can't pin both, because an absolute path short-circuits the `&&` before
  # public_repo? is ever evaluated. Don't consolidate these back to one test
  # each — that asymmetry is exactly why there are two.
  #
  # assert_nil passes trivially whenever the rule fails to fire for any
  # reason at all, so each assertion here is mutation-confirmed: drop any one
  # of the four conjuncts across these rules and exactly one test below fails.
  #
  # This is the fail-closed direction, so it leaks nothing. It blocks every
  # correctly-configured adopter instead, which is the failure this corpus is
  # supposed to make impossible to ship unnoticed.

  def test_public_filing_with_an_enabled_watchdog_is_fine
    cfg = base
    cfg['issue_capture']['max_public_files_per_run'] = 3
    assert_nil rule(cfg, :public_files_need_watchdog)
  end

  def test_contributor_code_with_a_sandbox_is_fine
    cfg = base
    cfg['secrets']['execute_contributor_code'] = true
    cfg['secrets']['sandbox_available'] = true
    assert_nil rule(cfg, :sandbox_required)
  end

  def test_absolute_index_path_in_a_public_repo_is_fine
    cfg = base
    cfg['repositories'][0]['visibility'] = 'public'
    cfg['scheduled_jobs']['outputs']['index_path'] = '/var/steward/index.md'
    assert_nil rule(cfg, :index_not_public)
  end

  def test_absolute_state_paths_in_a_public_repo_is_fine
    cfg = base
    cfg['repositories'][0]['visibility'] = 'public'
    assert_nil rule(cfg, :state_paths_not_public)
  end
end

# --- Entrypoint: the real bin, not just the helpers ---
class EntrypointTest < Minitest::Test
  BIN = File.expand_path('../bin/config-lint', __dir__)

  def run_lint(*args)
    out = IO.popen([BIN, *args], err: [:child, :out], &:read)
    [out, $CHILD_STATUS.exitstatus]
  end

  def test_no_args_checks_both_in_repo_configs_and_passes
    out, status = run_lint
    assert_equal 0, status, out
    assert_match(/config\.template\.yaml/, out)
    assert_match(/setup\/config\.yaml/, out)
    assert_match(/OK/, out)
  end

  def test_help_states_the_boundary
    out, status = run_lint('--help')
    assert_equal 0, status
    assert_match(/statically decidable subset/, out)
    assert_match(/does not replace/, out)
  end

  def test_unreadable_path_exits_1_rather_than_crashing
    out, status = run_lint('no/such/config.yaml')
    assert_equal 1, status
    assert_match(/could not read/, out)
  end
end

# --- Corpus: every rule has a fixture that provokes it ---
class CorpusTest < Minitest::Test
  BIN = File.expand_path('../bin/config-lint', __dir__)

  def self.red_fixtures
    Dir.glob(File.join(FIXTURE_ROOT, 'red-*.yaml')).sort
  end

  def rules_for(path)
    template = ConfigContract::Extract.load(
      File.expand_path('../setup/config.template.yaml', __dir__)
    )
    ConfigContract.check(config: ConfigContract::Extract.load(path), template: template)
                  .select { |v| v.severity == :error }.map(&:rule)
  end

  def test_the_corpus_is_not_empty
    floor = ConfigContract::SEMANTIC_RULES.size + 4
    assert_operator self.class.red_fixtures.size, :>=, floor,
                    'red fixtures went missing — the corpus proves nothing if it does not load'
  end

  def test_green_fixture_passes_through_the_real_entrypoint
    out = IO.popen([BIN, File.join(FIXTURE_ROOT, 'green-enabled.yaml')], err: [:child, :out], &:read)
    assert_equal 0, $CHILD_STATUS.exitstatus, out
  end

  red_fixtures.each do |path|
    name = File.basename(path, '.yaml')
    expected = name.sub(/\Ared-/, '').to_sym

    define_method("test_#{name}_provokes_#{expected}") do
      actual = rules_for(path)
      assert_includes actual, expected,
                      "#{name} did not provoke #{expected} — got #{actual.inspect}"
    end

    define_method("test_#{name}_exits_nonzero") do
      IO.popen([BIN, path], err: [:child, :out], &:read)
      assert_equal 1, $CHILD_STATUS.exitstatus
    end
  end
end

# --- Anti-vacuity: a rule with no fixture fails the suite ---
class MetaTest < Minitest::Test
  def test_every_semantic_rule_has_a_red_fixture
    covered = Dir.glob(File.join(FIXTURE_ROOT, 'red-*.yaml'))
                 .map { |p| File.basename(p, '.yaml').sub(/\Ared-/, '').to_sym }
    missing = ConfigContract::SEMANTIC_RULES - covered
    assert_empty missing,
                 "these rules have no red fixture, so nothing proves they fire: #{missing.inspect}"
  end
end

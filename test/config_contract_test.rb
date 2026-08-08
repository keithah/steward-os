# frozen_string_literal: true

require 'minitest/autorun'
require 'tmpdir'
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

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
    'project' => { 'name' => '' },
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

  def test_non_mapping_document_is_an_error
    v = ConfigContract.check(config: [], template: TEMPLATE).find { |x| x.rule == :not_a_mapping }
    assert_equal :error, v.severity
  end

  def test_shape_error_suppresses_later_phases
    cfg = Marshal.load(Marshal.dump(TEMPLATE))
    cfg['bogus'] = 1
    v = check(cfg)
    assert_includes rules(v), :unknown_key
    assert_includes rules(v), :phases_skipped
  end
end

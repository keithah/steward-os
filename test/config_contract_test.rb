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

# frozen_string_literal: true

require 'yaml'

# Enforces the fail-closed invariants that setup/config.template.yaml,
# docs/reference/security-spine.md #6, and skills/issue-capture/SKILL.md state in
# prose. Checks the statically decidable subset only — writability, reachability,
# and the privacy of destinations outside the repo stay with the runtime preflight.
#
# Split into a pure checker (ConfigContract.check — parsed hashes in, violations
# out) and an Extract module that reads the repo, mirroring lib/skills_contract.rb.
module ConfigContract
  Violation = Struct.new(:severity, :rule, :key, :message, keyword_init: true) do
    SYMBOL = { error: 'x', warning: '!', info: '·' }.freeze

    def to_line
      "  #{SYMBOL.fetch(severity)} #{key} — #{message}"
    end
  end

  # Job schedule keys alternate: a job needs exactly one. Exempt from the set
  # diff so job_schedule_alternation can check them per-job instead.
  SCHEDULE_KEYS = %w[scheduled_jobs.jobs[].every scheduled_jobs.jobs[].cron].freeze

  # Pure. Given the parsed config and the parsed template, return violations.
  #
  # Three ordered phases. Phase 3's reads all assume a key exists at the right
  # path with the right type, so a shape or type error suppresses it — that
  # ordering is the fail-closed mechanism, not an optimization.
  def self.check(config:, template:)
    return [Violation.new(severity: :error, rule: :not_a_mapping, key: '(document)',
                          message: 'config is empty or its top level is not a mapping')] unless config.is_a?(Hash) && !config.empty?

    violations = shape_violations(config, template)

    unless violations.any? { |v| v.severity == :error }
      # Type and semantic phases land here in Tasks 3 and 4.
    end

    if violations.any? { |v| v.severity == :error }
      violations << Violation.new(severity: :info, rule: :phases_skipped, key: '(document)',
                                  message: 'semantic invariants NOT evaluated — fix the errors above and re-run')
    end

    violations
  end

  # A template path is satisfied by a config path that equals it, or that
  # descends from it. Lets `sources: []` in one file accept `sources: [...]` in
  # the other, in both directions.
  def self.covered?(path, leaves)
    leaves.include?(path) ||
      leaves.any? { |l| l.start_with?("#{path}.") || l.start_with?("#{path}[") }
  end

  def self.shape_violations(config, template)
    cfg_leaves = Extract.key_paths(config).uniq
    tpl_leaves = Extract.key_paths(template).uniq
    violations = []

    (cfg_leaves - tpl_leaves).sort.each do |path|
      next if SCHEDULE_KEYS.include?(path)
      next if tpl_leaves.any? { |t| covered?(t, [path]) }

      violations << Violation.new(severity: :error, rule: :unknown_key, key: path,
                                  message: 'not a key in setup/config.template.yaml — check the spelling')
    end

    (tpl_leaves - cfg_leaves).sort.each do |path|
      next if SCHEDULE_KEYS.include?(path)
      next if covered?(path, cfg_leaves)

      violations << Violation.new(severity: :error, rule: :missing_key, key: path,
                                  message: 'required by setup/config.template.yaml but absent here')
    end

    violations + job_schedule_violations(config)
  end

  def self.job_schedule_violations(config)
    jobs = config.dig('scheduled_jobs', 'jobs')
    return [] unless jobs.is_a?(Array)

    jobs.each_with_index.filter_map do |job, i|
      next unless job.is_a?(Hash)

      set = %w[every cron].count { |k| job.key?(k) }
      next if set == 1

      name = job['name'].is_a?(String) ? job['name'] : "index #{i}"
      Violation.new(severity: :error, rule: :job_schedule_alternation,
                    key: "scheduled_jobs.jobs[#{i}]",
                    message: "job '#{name}' must set exactly one of every: or cron: (found #{set})")
    end
  end

  # Repo readers. Each is small and testable against real files.
  module Extract
    # Subtrees whose keys are adopter-defined (path globs, title prefixes, bucket
    # names). Descending would report every adopter's own keys as unknown.
    # Documented cost: a typo inside one of these goes unchecked.
    OPAQUE_SUBTREES = %w[
      labels.area.map
      labels.type.map
      labels.size.buckets
    ].freeze

    module_function

    # Parse a config. Raises a clear error rather than a bare Psych stacktrace.
    def load(path)
      YAML.safe_load(File.read(path)) || {}
    rescue Psych::Exception => e
      raise "could not parse #{path}: #{e.message}"
    end

    # Every leaf key path, dotted, with list elements normalized to `[]` so a
    # one-element template list and a five-element adopter list compare equal.
    # An empty Hash or Array is itself a leaf; check#covered? then treats it as
    # an opaque prefix so `sources: []` accepts `sources: ["email"]`.
    def key_paths(node, prefix = '')
      return [prefix] if !prefix.empty? && OPAQUE_SUBTREES.include?(prefix)

      case node
      when Hash
        return [prefix] if node.empty?

        node.flat_map { |k, v| key_paths(v, prefix.empty? ? k.to_s : "#{prefix}.#{k}") }
      when Array
        return [prefix] if node.empty?

        node.flat_map { |v| key_paths(v, "#{prefix}[]") }
      else
        [prefix]
      end
    end
  end
end

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

  BOOLEAN_KEYS = %w[
    issue_capture.enabled
    community.chat.monitor
    secrets.execute_contributor_code
    secrets.sandbox_available
  ].freeze

  STRING_KEYS = %w[
    issue_capture.capture_marker
    issue_capture.queue_path
    issue_capture.ledger_path
    issue_capture.checkpoint_path
    community.chat.platform
    community.chat.capture_reaction
    security.security_contact
    scheduled_jobs.outputs.index_path
    scheduled_jobs.outputs.digest_to
    scheduled_jobs.outputs.alarms_to
  ].freeze

  NON_NEGATIVE_INT_KEYS = %w[
    issue_capture.max_per_run
    issue_capture.max_public_files_per_run
  ].freeze

  VISIBILITIES = %w[public private].freeze

  # Every semantic rule this phase can emit. Task 6's meta-test asserts each has
  # a red fixture — keep in sync when adding a rule.
  SEMANTIC_RULES = %i[
    alarms_to_required
    capture_state_paths_required
    pull_index_required
    index_not_public
    marker_needs_watchdog
    reaction_needs_watchdog
    chat_needs_capture_core
    chat_needs_platform
    public_files_need_watchdog
    sandbox_required
  ].freeze

  CAPTURE_STATE_PATHS = %w[queue_path ledger_path checkpoint_path].freeze

  # Pure. Given the parsed config and the parsed template, return violations.
  #
  # Three ordered phases. Phase 3's reads all assume a key exists at the right
  # path with the right type, so a shape or type error suppresses it — that
  # ordering is the fail-closed mechanism, not an optimization.
  def self.check(config:, template:)
    unless config.is_a?(Hash) && !config.empty?
      # Nothing downstream can run, so say so — every suppressing error
      # announces the suppression, this one included.
      return [Violation.new(severity: :error, rule: :not_a_mapping, key: '(document)',
                            message: 'config is empty or its top level is not a mapping'),
              phases_skipped]
    end

    violations = shape_violations(config, template)

    unless violations.any? { |v| v.severity == :error }
      violations.concat(type_violations(config))
    end

    unless violations.any? { |v| v.severity == :error }
      violations.concat(semantic_violations(config))
    end

    violations << phases_skipped if violations.any? { |v| v.severity == :error }

    violations
  end

  def self.phases_skipped
    Violation.new(severity: :info, rule: :phases_skipped, key: '(document)',
                  message: 'semantic invariants NOT evaluated — fix the errors above and re-run')
  end

  # `leaf` sits strictly beneath `ancestor` in the path namespace.
  def self.strict_descendant_of?(leaf, ancestor)
    leaf.start_with?("#{ancestor}.") || leaf.start_with?("#{ancestor}[")
  end

  # A path is satisfied by a leaf that equals it or descends from it. This is
  # the template-empty/config-populated direction: `sources: []` in the
  # template accepts `sources: [...]` in the config. The opposite direction is
  # handled in shape_violations, gated on genuine emptiness.
  def self.covered?(path, leaves)
    leaves.include?(path) || leaves.any? { |l| strict_descendant_of?(l, path) }
  end

  def self.shape_violations(config, template)
    cfg_leaves = Extract.key_paths(config).uniq
    tpl_leaves = Extract.key_paths(template).uniq
    cfg_empty = Extract.empty_collection_paths(config).uniq
    violations = []

    (cfg_leaves - tpl_leaves).sort.each do |path|
      next if SCHEDULE_KEYS.include?(path)
      next if tpl_leaves.any? { |t| covered?(t, [path]) }
      # unknown-key loop — an empty collection here stands in for whatever the
      # template puts under it; the collection root is not an unknown key.
      next if cfg_empty.include?(path) && tpl_leaves.any? { |t| strict_descendant_of?(t, path) }

      violations << Violation.new(severity: :error, rule: :unknown_key, key: path,
                                  message: 'not a key in setup/config.template.yaml — check the spelling')
    end

    (tpl_leaves - cfg_leaves).sort.each do |path|
      next if SCHEDULE_KEYS.include?(path)
      next if covered?(path, cfg_leaves)
      # missing-key loop — ...and it accounts for the keys the template puts
      # beneath it. Gated on a genuinely empty [] or {}: a nil or scalar
      # ancestor keeps reporting, or this skip becomes a fail-open hole over
      # the whole subtree.
      next if cfg_empty.any? { |c| strict_descendant_of?(path, c) }

      violations << Violation.new(severity: :error, rule: :missing_key, key: path,
                                  message: 'required by setup/config.template.yaml but absent here')
    end

    violations + job_schedule_violations(config)
  end

  def self.job_schedule_violations(config)
    violations_for_each(config, 'scheduled_jobs', 'jobs') do |job, i|
      set = %w[every cron].count { |k| job.key?(k) }
      next [] if set == 1

      name = job['name'].is_a?(String) ? job['name'] : "index #{i}"
      [Violation.new(severity: :error, rule: :job_schedule_alternation,
                     key: "scheduled_jobs.jobs[#{i}]",
                     message: "job '#{name}' must set exactly one of every: or cron: (found #{set})")]
    end
  end

  # Yield each Hash element of the array at `path`, with its index, and
  # concatenate the violation arrays the block returns. The block always
  # returns an Array — `[]` means "nothing wrong with this element".
  #
  # Three call sites walk a list of hashes this way (job schedules, job types,
  # repository visibility). They share this instead of each rewriting the
  # walk. Walks the path by hand rather than with `dig`: dig raises TypeError
  # when an intermediate is a scalar, and a malformed `scheduled_jobs: yes`
  # must be reported by the shape or type phase, not crash the lint.
  def self.violations_for_each(config, *path)
    node = path.reduce(config) { |acc, key| acc.is_a?(Hash) ? acc[key] : nil }
    return [] unless node.is_a?(Array)

    node.each_with_index.flat_map do |item, index|
      item.is_a?(Hash) ? yield(item, index) : []
    end
  end

  # Dotted-path read for non-list paths. List members are walked explicitly.
  def self.fetch_path(hash, dotted)
    dotted.split('.').reduce(hash) do |node, key|
      return nil unless node.is_a?(Hash)

      node[key]
    end
  end

  def self.type_violations(config)
    violations = []

    BOOLEAN_KEYS.each do |path|
      value = fetch_path(config, path)
      next if [true, false].include?(value)

      violations << type_error(:type_boolean, path, value, 'true or false')
    end

    STRING_KEYS.each do |path|
      value = fetch_path(config, path)
      next if value.is_a?(String)

      violations << type_error(:type_string, path, value, 'a string (blank disables)')
    end

    NON_NEGATIVE_INT_KEYS.each do |path|
      value = fetch_path(config, path)
      next if value.is_a?(Integer) && !value.negative?

      violations << type_error(:type_non_negative_integer, path, value, 'an integer >= 0')
    end

    violations + repository_violations(config) + job_type_violations(config)
  end

  def self.type_error(rule, key, value, expected)
    Violation.new(severity: :error, rule: rule, key: key,
                  message: "expected #{expected}, got #{value.class} (#{value.inspect})")
  end

  def self.repository_violations(config)
    violations_for_each(config, 'repositories') do |repo, i|
      value = repo['visibility']
      next [] if VISIBILITIES.include?(value)

      [Violation.new(severity: :error, rule: :type_enum, key: "repositories[#{i}].visibility",
                     message: "expected one of #{VISIBILITIES.join(', ')}, got #{value.inspect}")]
    end
  end

  def self.job_type_violations(config)
    violations_for_each(config, 'scheduled_jobs', 'jobs') do |job, i|
      violations = []
      unless job['name'].is_a?(String)
        violations << type_error(:type_string, "scheduled_jobs.jobs[#{i}].name", job['name'], 'a string')
      end
      unless [true, false].include?(job['enabled'])
        violations << type_error(:type_boolean, "scheduled_jobs.jobs[#{i}].enabled", job['enabled'],
                                 'true or false')
      end
      violations
    end
  end

  def self.blank?(value)
    value.nil? || (value.is_a?(String) && value.strip.empty?)
  end

  # No URI scheme, not absolute, not home-relative. Anything else is outside the
  # repo and routes to the unverifiable warning instead of index_not_public.
  # A Windows-style C:\ path counts as outside — not a supported form, but
  # calling it repo-relative would produce a false error.
  def self.repo_relative?(path)
    return false if blank?(path)
    return false if path.include?('://') || path.start_with?('/', '~')
    return false if path.match?(/\A[A-Za-z]:[\\\/]/)

    true
  end

  def self.watchdog_enabled?(config)
    jobs = config.dig('scheduled_jobs', 'jobs')
    return false unless jobs.is_a?(Array)

    jobs.any? { |j| j.is_a?(Hash) && j['name'] == 'action-watchdog' && j['enabled'] == true }
  end

  def self.semantic_violations(config)
    capture = config.dig('issue_capture', 'enabled') == true
    monitor = config.dig('community', 'chat', 'monitor') == true
    marker  = config.dig('issue_capture', 'capture_marker')
    reaction = config.dig('community', 'chat', 'capture_reaction')
    outputs = config.dig('scheduled_jobs', 'outputs') || {}
    index   = outputs['index_path']
    watchdog = watchdog_enabled?(config)

    violations = []

    if capture
      if blank?(outputs['alarms_to'])
        violations << err(:alarms_to_required, 'scheduled_jobs.outputs.alarms_to',
                          'required when issue_capture.enabled — the divert needs an independent ' \
                          'human route even when security_contact is set (config.template.yaml:130)')
      end

      missing = CAPTURE_STATE_PATHS.select { |k| blank?(config.dig('issue_capture', k)) }
      unless missing.empty?
        violations << err(:capture_state_paths_required, 'issue_capture',
                          "issue_capture.enabled requires private #{missing.join(', ')} " \
                          '(issue-capture/SKILL.md:19-21)')
      end

      if blank?(index)
        violations << err(:pull_index_required, 'scheduled_jobs.outputs.index_path',
                          'issue_capture.enabled requires a pull index (issue-capture/SKILL.md:26-27)')
      elsif repo_relative?(index) && public_repo?(config)
        violations << err(:index_not_public, 'scheduled_jobs.outputs.index_path',
                          "'#{index}' is repo-relative and this project lists a public repository. " \
                          'An index that can hold a diverted vulnerability must not be a public ' \
                          'surface (config.template.yaml:124-126). If it lives in a private repo, ' \
                          'use an absolute path to say so')
      end
    end

    unless blank?(marker) || watchdog
      violations << err(:marker_needs_watchdog, 'issue_capture.capture_marker',
                        'a public capture marker requires an enabled action-watchdog job ' \
                        '(config.template.yaml:51)')
    end

    if monitor && !blank?(reaction) && !watchdog
      violations << err(:reaction_needs_watchdog, 'community.chat.capture_reaction',
                        'a public capture reaction requires an enabled action-watchdog job ' \
                        '(issue-capture/SKILL.md:23-25)')
    end

    if monitor && !capture
      violations << err(:chat_needs_capture_core, 'community.chat.monitor',
                        'chat monitoring requires issue_capture.enabled — chat must not implement ' \
                        'a second capture path (config.template.yaml:44)')
    end

    if monitor && blank?(config.dig('community', 'chat', 'platform'))
      violations << err(:chat_needs_platform, 'community.chat.platform',
                        'a blank platform means chat monitoring is disabled, but monitor is true')
    end

    files = config.dig('issue_capture', 'max_public_files_per_run')
    if files.is_a?(Integer) && files.positive? && !watchdog
      violations << err(:public_files_need_watchdog, 'issue_capture.max_public_files_per_run',
                        'autonomous public filing requires an enabled action-watchdog ' \
                        '(INFERRED from security-spine.md #6 self-test, not a literal config line)')
    end

    if config.dig('secrets', 'execute_contributor_code') == true &&
       config.dig('secrets', 'sandbox_available') != true
      violations << err(:sandbox_required, 'secrets.execute_contributor_code',
                        'running contributor code requires sandbox_available: true ' \
                        '(config.template.yaml:106)')
    end

    violations + unverifiable_warning(config, capture, outputs, index)
  end

  def self.err(rule, key, message)
    Violation.new(severity: :error, rule: rule, key: key, message: message)
  end

  def self.public_repo?(config)
    repos = config['repositories']
    return false unless repos.is_a?(Array)

    repos.any? { |r| r.is_a?(Hash) && r['visibility'] == 'public' }
  end

  # One grouped warning, not one per destination: three separate warnings would
  # fire on every correctly-configured adopter and train them to ignore output.
  def self.unverifiable_warning(_config, capture, outputs, index)
    return [] unless capture

    unverifiable = []
    unverifiable << "index_path '#{index}' (outside the repo)" if !blank?(index) && !repo_relative?(index)
    unverifiable << "digest_to '#{outputs['digest_to']}' (channel privacy not statically decidable)" unless blank?(outputs['digest_to'])
    unverifiable << "alarms_to '#{outputs['alarms_to']}' (channel privacy not statically decidable)" unless blank?(outputs['alarms_to'])
    return [] if unverifiable.empty?

    [Violation.new(severity: :warning, rule: :destinations_unverifiable, key: 'scheduled_jobs.outputs',
                   message: "#{unverifiable.size} destination(s) could not be verified as private:\n" \
                            "#{unverifiable.map { |u| "      #{u}" }.join("\n")}\n" \
                            '    Confirm each before enabling autonomous capture.')]
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

    # Every leaf path paired with its node. key_paths and
    # empty_collection_paths are both views of this one walk.
    def leaves(node, prefix = '')
      return [[prefix, node]] if !prefix.empty? && OPAQUE_SUBTREES.include?(prefix)

      case node
      when Hash
        return [[prefix, node]] if node.empty?

        node.flat_map { |k, v| leaves(v, prefix.empty? ? k.to_s : "#{prefix}.#{k}") }
      when Array
        return [[prefix, node]] if node.empty?

        node.flat_map { |v| leaves(v, "#{prefix}[]") }
      else
        [[prefix, node]]
      end
    end

    # Every leaf key path, dotted, with list elements normalized to `[]` so a
    # one-element template list and a five-element adopter list compare equal.
    # An empty Hash or Array is itself a leaf.
    def key_paths(node, prefix = '')
      leaves(node, prefix).map(&:first)
    end

    # The subset of leaf paths whose node is an empty [] or {}. An empty
    # collection is a leaf that stands in for whatever the other file puts
    # beneath it; nil and scalars deliberately do not qualify.
    def empty_collection_paths(node, prefix = '')
      leaves(node, prefix).filter_map do |path, value|
        path if (value.is_a?(Hash) || value.is_a?(Array)) && value.empty?
      end
    end
  end
end

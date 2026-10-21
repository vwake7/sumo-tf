# Sumo Logic Terraform Import Generator — Specification

## 1. Purpose

Build a generator that inventories existing Sumo Logic objects and produces Terraform configuration and import declarations so those objects can be managed as infrastructure as code going forward.

The primary success criterion is that, after import, Terraform reports no unintended changes for supported objects. A generated configuration is a proposal to review; the generator must not apply it automatically.

## 2. Goals

- Discover deployed Sumo Logic objects through StackQL's Sumo provider.
- Use the installed Sumo Terraform provider's machine-readable schema as the authority for Terraform resource and attribute semantics.
- Correlate discovered API objects with Terraform resource types and their configurable attributes.
- Generate readable `.tf` files and Terraform `import` blocks for supported objects.
- Make unsupported, ambiguous, or lossy mappings visible for human review.
- Support a validation workflow ending in `terraform plan` and a zero-diff result where the provider/API behavior permits it.

## 3. Non-goals

- Reimplement the Sumo Logic API or Terraform provider.
- Assume StackQL and Terraform have identical resource models or schemas.
- Automatically create Terraform resources where no reliable mapping exists.
- Run `terraform apply`, mutate the Sumo account, or automatically import state as a side effect of generation.
- Guarantee zero diff for resources whose provider behavior, defaults, normalization, or API representation prevents it.
- Support every StackQL resource in the initial release.

## 4. Concepts and source of truth

StackQL resources represent API operations and API-shaped data. Terraform resources represent provider-defined desired state. Their resource and field names can differ, and the mapping is not necessarily one-to-one.

The generator uses each source for a separate purpose:

- **StackQL inventory:** evidence of objects and API-returned values currently present in the account.
- **Terraform provider schema:** authority for supported Terraform resource types, attribute names, nesting, and `required`, `optional`, and `computed` semantics.
- **Mapping metadata:** explicit association between StackQL service/resource/operation and a Terraform resource type, including identity/import handling and any field transformations that cannot be inferred safely.

The generator must not treat an API response schema as a Terraform configuration schema.

## 5. User workflow

1. The user configures StackQL credentials and installs/configures the Sumo Terraform provider.
2. The user selects StackQL resources or services to inventory, optionally with query filters.
3. The tool collects results and saves a reproducible inventory artifact (JSON).
4. The tool obtains Terraform provider schema JSON, normally through `terraform providers schema -json` in an initialized Terraform working directory.
5. The tool matches inventory objects to Terraform resource types and attributes.
6. The tool writes generated Terraform configuration, import blocks, and a report of skipped or uncertain mappings into an output directory.
7. The user reviews the files, initializes/configures Terraform as needed, and runs `terraform plan`.
8. The user resolves reported mismatches and applies/imports through their normal Terraform workflow.

## 6. Functional requirements

### 6.1 Inventory collection

- Accept StackQL query results as JSON input. The initial implementation may invoke StackQL externally or consume exported JSON; the interface must keep collection separate from generation.
- Preserve the originating StackQL service, resource, query, and collection timestamp with each inventory set.
- Preserve each object's raw response so mappings can be debugged and the inventory can be reprocessed without querying the account again.
- Allow users to scope inventory by service/resource and provide query filters; do not silently expand a request into a full-account scan.
- Handle pagination and partial query failures explicitly if the collector invokes StackQL. A partial inventory must be marked incomplete and must not be presented as a complete scan.

### 6.2 Terraform schema acquisition

- Consume the JSON emitted by `terraform providers schema -json`.
- Identify the Sumo provider by its fully qualified provider address, configurable where needed.
- Extract resource schemas recursively, including nested attributes/blocks, types, required/optional/computed flags, sensitivity, and descriptions when present.
- Fail with an actionable diagnostic if the schema is missing, invalid, or does not contain the expected provider.
- Record the Terraform/provider version associated with the schema when available.

### 6.3 Resource mapping

- Support explicit mappings from StackQL resource identity to Terraform resource type.
- Permit one StackQL resource to map to multiple Terraform types, multiple API resources to map to one Terraform type, or no mapping, where the mapping metadata explicitly describes this.
- Do not infer a resource mapping solely from similar names. Name-based candidates may be surfaced for review but cannot be treated as verified mappings.
- Report unmapped and ambiguous resources without generating Terraform for them.
- Keep mapping data versioned and inspectable (for example, YAML or JSON); mappings must identify their source and any assumptions.

### 6.4 Attribute mapping and classification

- Match fields only within a verified resource mapping.
- Prefer explicit field mappings, including API wire-name conversion, over generic name normalization.
- Generic matching may propose candidates (such as `parentId` to `parent_id`) but must not silently map when more than one candidate exists or the transformation is not lossless.
- Emit only Terraform attributes that can be set in configuration: required and optional attributes, subject to the provider's schema and resource-specific mapping rules.
- Never emit computed-only attributes as configuration arguments. Keep computed values available in diagnostics when useful.
- Omit API response metadata that has no configurable Terraform counterpart, such as creation timestamps or creator identity, unless a specific provider schema declares it configurable.
- For unknown API fields, incompatible types, nulls, or lossy conversions, report the field and reason; do not invent a value or silently coerce it.
- Preserve distinction between an omitted field, an explicit `null`, an empty string, an empty collection, and a zero/false value when the provider schema makes those distinctions relevant.
- Support nested Terraform blocks/attributes according to the provider schema and explicit mapping instructions; do not flatten structures unless the mapping defines a reversible transformation.

### 6.5 Identity and import generation

- Each generated resource must have a stable, valid Terraform local name derived from a configurable naming policy.
- Detect and resolve local-name collisions deterministically, while recording the original remote identity in the report.
- Generate an import block only when the mapping defines a reliable Terraform import ID for the object.
- Treat import IDs as provider-specific; do not assume that an API `id` field is the Terraform import ID.
- If an import ID cannot be determined reliably, omit that object's resource and import block and report the reason.
- Keep resource address and import target consistent.

### 6.6 Output artifacts

For each run, emit:

- Terraform `.tf` configuration for supported objects.
- Terraform `import` blocks for objects with verified import IDs.
- A machine-readable report (JSON) containing generated, skipped, ambiguous, and warning outcomes.
- A copy or reference to the input inventory and schema provenance sufficient to reproduce the run.

Output must be deterministic for identical inputs and mapping metadata. Generated HCL should be formatted consistently and should quote/escape strings and identifiers correctly.

### 6.7 Validation and plan review

- Document and support the user running `terraform fmt` and `terraform validate` on generated configuration.
- Provide a clear path to run `terraform plan` after import and inspect differences.
- The target outcome for a correctly mapped and imported existing object is a plan with no unintended changes (`0 to add, 0 to change, 0 to destroy`).
- Explain plan differences as validation findings; do not automatically rewrite configuration based on plan output in the initial release.
- Never run `terraform apply` as part of generation or validation.

## 7. Suggested architecture

```text
StackQL query results ──> Inventory JSON ──┐
                                           ├─> Matcher / transformer ─> Terraform files
Terraform schema JSON ─────────────────────┤                         └> Report JSON
Versioned mapping metadata ────────────────┘
```

Recommended components:

1. **Inventory adapter:** normalizes StackQL query output while retaining raw objects and provenance.
2. **Schema loader:** parses Terraform provider schema JSON into an internal schema model.
3. **Mapping registry:** loads verified resource, attribute, and import-ID rules.
4. **Matcher/transformer:** classifies fields and converts supported values into Terraform-ready data.
5. **HCL renderer:** renders deterministic resource and import blocks.
6. **Run reporter:** records decisions, warnings, skipped objects, and input versions.

The components should be usable independently so saved inventory/schema files can be reprocessed without credentials or API access.

## 8. Error handling and safety

- Validate all input files before writing output.
- Write to a user-selected output directory and avoid overwriting existing files without an explicit overwrite option.
- Include object-level errors in the report and continue processing independent objects where safe.
- Mark a run unsuccessful when required inputs are invalid or the inventory is incomplete; never label partial output as a complete account export.
- Do not log secrets, access tokens, or credential-bearing connection strings.
- Do not perform account mutations or Terraform apply operations.

## 9. Acceptance criteria

1. Given StackQL inventory, Terraform schema JSON, and a verified mapping, the generator produces valid, deterministic HCL for supported objects.
2. Generated configuration contains only attributes that are configurable according to the Terraform schema and mapping rules.
3. Computed-only fields and unmapped API fields are excluded from HCL and explained in the report.
4. Ambiguous resource, field, or import-ID mappings are reported and do not produce unsafe Terraform resources.
5. Import blocks use the mapped Terraform resource address and provider-correct import ID.
6. Re-running with identical inputs produces the same output.
7. The documented workflow allows a user to run Terraform validation and plan, with zero diff as the target for accurately mapped resources.
8. Generation does not execute `terraform apply` or mutate the Sumo account.

## 10. Initial delivery boundary

The first usable release should support a deliberately small set of high-value Sumo resources with verified resource mappings, field mappings, and import-ID rules. It should establish the inventory/schema/report formats and prove the end-to-end workflow on representative resources before expanding coverage. StackQL's full catalog is not an initial coverage commitment.

## 11. Open decisions

- Which implementation language and distribution format will the project use?
- Will the first release query StackQL directly, or require exported StackQL JSON?
- Which Sumo Terraform provider version is the initial compatibility target?
- Which resources form the first supported vertical slice?
- Where will verified mapping metadata live, and what review process governs updates?
- Should generated files be grouped by resource type, service, or object?
- What naming policy should be the default for Terraform local names?
- Is Colab a required runtime, or merely the current prototype environment?


# Combined P1 application candidate

Tracks `enhancedchannelmanager-tj3d7`, integration build `0.18.2-0063`.

This isolated branch combines the reviewed application candidates from protected
`dev` base `0bc126006d96c03637cb9e1b707ec04aca2d5804`:

| Original PR | Reviewed head | Scope |
| --- | --- | --- |
| #1051 | `0f8c83ba19e6e805cd17332be0dca3ad8c22d628` | Signed MCP confirmation preparation |
| #1052 | `23398e0874d1776c7e27363a187f026769821331` | Confirmed bounded profile membership saves |
| #1053 | `aa1f5b14ed658d69dfe68a33bc194cec3e1a0769` | Cooperative pipeline evaluation and cancellation-safe journal cleanup |
| #1054 | `d19621357dd38b0e6913951c4a46105d06a0052e` | Shared route-validated local probe admission, explicitly partial |

Original branches, PRs and unrelated work are preserved. Merge conflicts were
limited to changelog/version/generated-SBOM touchpoints. Probe-result reporting
and responsiveness changes in the pipeline engine occupy separate methods; both
sets of behavior must remain intact in the combined regression run.

All version touchpoints use build 0063 and the generated dev source SBOM must
match the combined tree. Original changelog entries retain their candidate build
numbers as provenance, not four simultaneously deployed versions.

## Verification requirements

Run the focused seam suites and complete canonical backend gate on the combined
tree, not by aggregating independent branch results. Run the full frontend
coverage suite, lint/typecheck/build, complete MCP suite, and explicit registered
SDK-tool to loopback backend confirmation/replay check. Check source integrity
against the original heads, including the two pipeline methods shared with the
probe lane. Protected CI and review apply to the integration head itself.

No source-combination result substitutes for actual image/native verification.
The last fresh read of the pinned base returned `404 MANIFEST_UNKNOWN`; both
authenticated ARM repositories still contained the old cohort below the vendor
CA/Brotli fix floors. `8mhga`, `97j9u` and `l5z6r` remain separate delivery holds.
No base, native package, source-cohort or trust-policy change is bundled here.

## Probe acceptance remains partial

Local reservations cannot protect a viewer arriving through Dispatcharr while
an admitted direct probe is already active. This candidate neither silently
changes routing nor authorizes a reserved-slot policy. Read
[the coordination boundary](direct-probe-coordination-limits.md) before making
any stronger safety claim. GH1032 remains open pending upstream coordination or
an explicitly reviewed product/policy decision.

This is a draft integration candidate, not authorization to merge a protected
branch, publish an unverified image, deploy, or waive scan gates.

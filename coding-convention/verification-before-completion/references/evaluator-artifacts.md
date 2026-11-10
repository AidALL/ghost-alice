# Evaluator Artifact Contract

Before claiming a result that adversarial-verification reviewed at level-3-high-stakes, or claiming external governance absorption or evaluator-candidate promotion, the response model reads `docs/policies/evaluator-artifact-contract.md` from the install source, the directory in the `source_root` field of `~/.ghost-alice/install-state/<platform>.json`. That policy defines the evaluation procedure and required evidence.

Use the verifier's judgment in `verifier-result.json` under that policy's `verifier_accepted` and `verifier_accepted_candidates` fields for the claimed evaluation. Follow the policy's reading rules for older `accepted` and `accepted_candidates` fields. Missing required evidence leaves the corresponding claim unresolved.

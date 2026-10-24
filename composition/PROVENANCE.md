# Source and modification provenance

Conductor Experimental is a public-only extraction of an existing local Conductor experiment associated with the Agent Control Plane project. Original private Git history, runtime implementation, operator configuration, task databases and audit reports are not included. This is not a clean-room implementation or an independently investigated ownership certificate.

## Component lineage

- Planner: original standalone, proposal-only scheduling experiment.
- Runtime: original local attempt store, subprocess adapter and optional integration glue for a separately supplied Hermes runtime. Hermes itself is not vendored.
- Composition: original fixed-plan composition using the planner/runtime public APIs, not an external platform lifecycle owner.
- Replanning: original bounded plan-admission journal using planner task types.
- Resolution: a version-isolated derivative of the runtime Store/process implementation. Request/Grant reuse runtime public contracts; the isolated successor adds explicitly authorized UNKNOWN-to-FAILED resolution and a separate host. Version 0.0.3 repairs terminal readback: an authorized FAILED outcome remains terminal when historical artifact integrity later fails; corrupt artifacts still cannot be consumed as successful results. The derivative Store/process source files carry prominent modification notices.

## Public-extraction changes

Public preparation adds SPDX headers, source/modification notices, public setup/security/contribution documentation, hashed development-tool requirements and minimal CI/verification scripts. It adds this provenance document to every package and ships it with Apache-2.0 in wheels and source distributions. It does not change the original execution/verification implementation or its experimental acceptance status. Historical component specifications remain development constraints, not grants to execute external actions or activate a live runtime.

## Rights and attribution

The publishing rights holder explicitly confirmed authority to release the additions and derivative modifications under Apache-2.0 and confirmed no undisclosed confidentiality restrictions. That human assertion is not an independent legal investigation. Existing Apache-2.0 license texts are preserved; this extraction invents no upstream author names, copyright assignments or employer approvals. No upstream NOTICE file was present in the included component inputs; this provenance document does not override any applicable third-party rights.

Core distributions declare only dependencies on the other included Conductor experimental packages; their implementation uses Python's standard library. Optional Hermes use requires a separate installation and its own applicable terms. Development tools and GitHub Actions are not vendored into the core distributions and retain their own licenses. Public source availability, successful CI and a publication-scope review do not constitute product certification; the earlier resolution product-result HOLD is not overturned.

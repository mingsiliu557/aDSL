# Overhang checker paired CPU pilot

## Material Passport

- Material ID: `adsl-overhang-feedback-pilot-20260906`
- Type: code experiment plan
- Origin Skill: `academic-research-suite/experiment-agent`
- Origin Mode: `run`
- Verification Status: PLANNED
- Generated (UTC): 2026-09-06

## Research question

Given an aDSL model produced from an original CAP3D or MARVEL caption, does
adding localized PrusaSlicer overhang feedback let the same Agent reduce actual
support-contact burden without deleting prompt-required geometry or changing
scale, orientation, or the slicer profile?

## Variables

- Independent variable: original aDSL workflow versus the same frozen
  `source.py` with a required overhang checker.
- Primary dependent variable: nominal model/support contact area in mm2.
- Secondary dependent variables: geometric overhang area, support base/interface
  path length, bridge path length, maximum bridge span, and support-required flag.
- Preservation evidence: matched Eevee views, source diff, prompt-feature audit,
  and fixed print-space AABB.
- Controls: identical baseline source, Z-up orientation, semantic scale, 180 mm
  print cap, nozzle, layer height, material profile, support policy, model
  profile, and render settings.

## Sampling

Four object instances are selected before generation by semantic feature filters
and deterministic hash order. Each contributes its CAP3D and MARVEL level-2
caption, for eight paired prompts. The pilot uses three ShapeNet objects and one
ABO object. Objaverse is explicitly excluded because its required MARVEL
annotation cannot be accessed as a small subset.

## Analysis

Report every generated baseline, including infrastructure failures and
zero-contact exclusions. For eligible cases, report before/after absolute values,
percentage changes, checker outcome, Agent patch, and visible appearance change.
Do not pool CAP3D and MARVEL prompts from the same object as independent object
instances when describing sample size.

## Stopping rule

A case stops at the first selected round that reduces nominal contact area by at
least 1%, does not increase geometric overhang area, and preserves print AABB
within 0.01 mm. Improvement is sufficient; no extra optimization round is
required. Checker ERROR is infrastructure failure, not model failure.

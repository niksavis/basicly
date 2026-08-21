- **Each agent role now declares the skills its purpose names.** Fourteen of the
  twenty model-invoked skills reached no role, and five roles declared none at all,
  so guidance the engine inlines into a dispatch prompt never arrived. `catalog
  lint` now reports any model-invoked skill no agent declares, against an exemption
  list that names the operator and environment skills a lane role must not carry
  and says why (`basicly-sromom`).

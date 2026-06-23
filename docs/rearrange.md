model2_package/
  common/
    config.py
    types.py
    ids.py

  A_state/
    exogenous_inputs.py
    state_builder.py
    baseline_flow_builder.py
    scenario_world.py
    flow_engine.py

  B_interpret/
    helper_calculator.py
    feature_builder.py
    prediction_attach.py
    risk_rule_builder.py
    need_signal.py

  C_policy_action/
    candidate_policy.py
    gate_policy.py
    simulator.py
    scenario_summary.py
    selector.py
    action_translator.py
    explain_memo.py

  runner/
    pipeline_orchestrator.py
    cli_run_all_in_one.py
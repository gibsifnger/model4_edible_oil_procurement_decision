from .C_policy_action import simulator as _new

simulate_candidate_under_scenario = _new.simulate_candidate_under_scenario
run_candidate_simulations = _new.run_candidate_simulations

def __getattr__(name):
    return getattr(_new, name)
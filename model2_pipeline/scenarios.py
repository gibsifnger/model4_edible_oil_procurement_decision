from .A_state import scenario_world as _new

shift_month_quantity = _new.shift_month_quantity
apply_scenario = _new.apply_scenario

def __getattr__(name):
    return getattr(_new, name)
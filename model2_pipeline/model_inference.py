from .B_interpret import prediction_attach as _new

ModelBundle = _new.ModelBundle
fit_demo_hgb_bundle = _new.fit_demo_hgb_bundle
save_model_bundle = _new.save_model_bundle
load_model_bundle = _new.load_model_bundle
attach_target_predictions = _new.attach_target_predictions

def __getattr__(name):
    return getattr(_new, name)
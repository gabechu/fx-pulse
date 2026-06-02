"""Offline training + online inference for the oversold/overbought classifier.

Two binary classifiers (buy, sell) over the feature registry in
`fx_pulse.features`. Calibrated probabilities, thresholded for high
precision (default >=90%), default to NO_DECISION otherwise.
"""

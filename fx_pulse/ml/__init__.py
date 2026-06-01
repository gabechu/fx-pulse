"""Offline training + online inference for the oversold/overbought classifier.

Two binary classifiers (buy, sell) over engineered features computed on
1-minute mid-price bars. Calibrated probabilities, thresholded for high
precision (default >=90%), default to NO_DECISION otherwise.
"""

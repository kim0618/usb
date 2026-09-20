"""Strategy E1 - premarket to opening-momentum pre-validation.

An independent research experiment. It asks whether information observable in the premarket up
to 09:25 ET selects the stocks that rise during the first 1, 5 and 15 minutes of the regular
session. E1 is not a retune of E0: E0 asked a different question, is CLOSED as FAIL, is not
modified here, and none of its findings enter an E1 feature or rule. E0's daily point-in-time
universe machinery and its statistics module are imported unchanged, as infrastructure only.
"""

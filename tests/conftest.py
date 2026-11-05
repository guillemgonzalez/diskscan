import os

# Never hit PyPI from the test suite.
os.environ["DISKSCAN_NO_UPDATE_CHECK"] = "1"

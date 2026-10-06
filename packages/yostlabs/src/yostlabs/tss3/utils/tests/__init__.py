# Importing a test module registers its test in SensorTest.REGISTERED, in import order
from yostlabs.tss3.utils.tests import (
    test_self,
    test_led,
    test_components,
    test_battery,
    test_rtc,
    test_button,
    test_gps,
    test_sd,
    test_overall,
)

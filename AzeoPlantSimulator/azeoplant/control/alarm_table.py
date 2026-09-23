# -*- coding: utf-8 -*-
"""The configured alarm schedule, generated from the Alarms sheet of
the plant alarm schedule. Regenerate rather than
hand-edit: tag -> list of (type, setpoint, priority, deadband, delay_s).
DISCRETE setpoints are the abnormal boolean state as 0/1.
"""

ALARMS = {
    "PT-0101": [("LO", 8, "High", 0.2, 5), ("LO_LO", 5, "Critical", 0.2, 2), ("HI_HI", 22, "Critical", 0.2, 2)],
    "AT-0101": [("DEV", 2, "Advisory", 0.2, 60)],
    "PT-0104": [("LO", 5.5, "Critical", 0.1, 3)],
    "FT-0102": [("LO", 600, "Advisory", 20, 30)],
    "LT-1001": [("LO_LO", 8, "Critical", 2, 3), ("LO", 20, "High", 2, 3), ("HI", 85, "High", 2, 3), ("HI_HI", 92, "Critical", 2, 3)],
    "PT-1002": [("HI", 26, "High", 0.3, 3)],
    "FT-1003": [("LO", 12, "High", 1, 10)],
    "FT-1004": [("LO", 20, "High", 2, 10)],          # loss of feed at the battery limit
    "PDT-1001": [("HI", 1.2, "Advisory", 0.05, 10)],
    "VT-1001": [("HI", 55, "High", 3, 5)],
    "IT-1001": [("HI", 165, "High", 4, 5)],
    "PT-2001": [("LO_LO", 2.5, "Critical", 0.1, 2)],
    "PT-2002": [("HI_HI", 26, "Critical", 0.3, 2)],
    "TT-2002": [("HI", 165, "High", 2, 5), ("HI_HI", 185, "Critical", 2, 3)],
    "TT-2003": [("HI", 95, "High", 2, 10)],
    "VT-2001": [("HI", 75, "High", 3, 3), ("HI_HI", 110, "Critical", 3, 1)],
    "PT-2003": [("LO", 1.8, "Critical", 0.1, 2)],
    "LT-2001": [("HI_HI", 85, "Critical", 2, 3)],
    "UIC-2001": [("DEV", 5, "High", 1, 2), ("DISCRETE", 1, "Critical", 0, 0)],
    "TT-3001": [("HI", 370, "High", 3, 5), ("HI_HI", 395, "Critical", 3, 3)],
    "TT-3005": [("HI_HI", 640, "Critical", 5, 3)],
    "TT-3004": [("HI", 430, "Advisory", 5, 10)],
    "PT-3001": [("LO_LO", 1.5, "Critical", 0.1, 1)],
    "AT-3001": [("LO", 1.5, "High", 0.2, 10)],
    "AT-3002": [("HI", 250, "High", 20, 15)],
    "BS-3001": [("DISCRETE", 0, "Critical", 0, 1)],
    "PT-3003": [("HI", 0, "High", 1, 5)],
    "TT-4002": [("HI", 430, "High", 3, 3), ("HI_HI", 470, "Critical", 3, 1)],
    "PDT-4001": [("HI", 3.2, "High", 0.1, 30)],
    "PT-4001": [("HI_HI", 54, "Critical", 0.3, 2)],
    "LT-4001": [("LO_LO", 12, "Critical", 2, 3), ("HI_HI", 88, "Critical", 2, 3)],
    "LT-4002": [("HI", 70, "High", 3, 10)],
    "AT-4001": [("HI", 120, "High", 10, 120)],
    "PT-5001": [("HI_HI", 12, "Critical", 0.1, 2)],
    "PDT-5001": [("HI", 340, "High", 10, 10), ("HI_HI", 420, "Critical", 10, 5)],
    "LT-5001": [("LO_LO", 10, "Critical", 2, 3), ("HI_HI", 90, "Critical", 2, 3)],
    "LT-5002": [("LO_LO", 10, "Critical", 2, 3)],
    "LT-5003": [("HI", 75, "High", 3, 10)],
    "AT-5001": [("HI", 2.5, "High", 0.2, 60)],
    "AT-5002": [("HI", 3, "High", 0.2, 60)],
    "PT-6001": [("HI_HI", 8.5, "Critical", 0.1, 2)],
    "PDT-6001": [("HI", 330, "High", 10, 10)],
    "LT-6001": [("LO_LO", 10, "Critical", 2, 3)],
    "LT-6002": [("HI", 85, "High", 2, 5)],
    # AT-6001 reads heavy key CONTENT on a 0-10 mol% analyser, so a
    # LO limit of 97 was a purity spec written against an impurity
    # tag: off the top of the scale, unclearable, and annunciating
    # the opposite of what it said. Its T1 counterpart AT-5001 has
    # always been HI 2.5; match it.
    "AT-6001": [("HI", 2.5, "High", 0.2, 60)],
    # T2 bottoms light key: the product limit the endurance test holds
    # it to (3 mol%) had no alarm behind it, so a 4.7 mol% excursion in
    # the 24 h soak annunciated nothing (2026-09-05). Same as AT-5002.
    "AT-6002": [("HI", 3, "High", 0.2, 60)],
    "FT-6004": [("HI", 140, "Advisory", 3, 120)],
    "LT-7001": [("LO_LO", -200, "Critical", 10, 2), ("LO", -120, "High", 10, 3), ("HI", 120, "High", 10, 3), ("HI_HI", 200, "Critical", 10, 2)],
    "PT-7001": [("HI_HI", 52, "Critical", 0.5, 2)],
    "PT-7002": [("LO", 32, "High", 0.5, 10)],
    "AT-7001": [("LO", 1.5, "High", 0.2, 10)],
    "AT-7002": [("HI", 250, "High", 20, 15)],
    "CT-7001": [("HI", 3500, "Advisory", 100, 60)],
    "BS-7001": [("DISCRETE", 0, "Critical", 0, 1)],
    "AT-8002": [("LO_LO", 5.5, "Critical", 0.1, 10), ("LO", 6, "High", 0.1, 30), ("HI", 8.5, "High", 0.1, 30), ("HI_HI", 9, "Critical", 0.1, 10)],
    "AT-8003": [("HI", 600, "High", 20, 120)],
    "LT-8001": [("HI_HI", 90, "High", 2, 5)],
    "HS-9002": [("DISCRETE", 1, "Critical", 0, 0)],
    "GD-9001": [("DISCRETE", 1, "Critical", 0, 0)],
    "GD-9002": [("DISCRETE", 1, "Critical", 0, 0)],
    "FD-9001": [("DISCRETE", 1, "Critical", 0, 0)],
}

# APVC control modules

This schedule is generated from `control_modules.json`. Edit and review the
machine-readable basis, then run `tools/render_control_module_basis.py`.
Gains are percent output per percent PV span; reset values are seconds.

**80 regulatory modules.**

| Module | PV | Output | Acting | Mode | Master | Gain | Reset | Role |
|---|---|---|---|---|---|---|---|---|
| AIC-0103 | AT-0103 | FCV-0102 | direct | AUTO | - | 1.00 | 900 | CT1 basin conductivity blowdown |
| AIC-3001 | AT-3001 | cascade SP | reverse | AUTO | - | 0.84 | 240 | H1 flue oxygen trims the air ratio |
| AIC-4001 | AT-4001 | cascade SP | direct | AUTO | - | 1.50 | 1800 | R1 product impurity trims bed temperature |
| AIC-5001 | AT-5001 | cascade SP | reverse | AUTO | - | 0.40 | 1200 | T1 distillate quality trims tray temperature |
| AIC-5002 | AT-5002 | cascade SP | direct | AUTO | - | 0.40 | 1200 | T1 bottoms quality trims sump temperature |
| AIC-6001 | AT-6001 | cascade SP | reverse | AUTO | - | 0.40 | 1200 | T2 distillate quality trims tray temperature |
| AIC-6002 | AT-6002 | cascade SP | direct | AUTO | - | 0.40 | 1200 | T2 bottoms quality trims sump temperature |
| AIC-7001 | AT-7001 | cascade SP | reverse | AUTO | - | 0.84 | 240 | B1 flue oxygen trims the air ratio |
| AIC-7002 | AT-7002 | cascade SP | direct | AUTO | - | 30.00 | 90 | override, parked at 0 |
| AIC-8001 | AT-8002 | split: FCV-8003/FCV-8002/FCV-8001 | reverse | AUTO | - | 1.12 | 240 | Outlet pH, split range reagents |
| CIC-7001 | CT-7001 | FCV-7004 | direct | AUTO | - | 1.00 | 600 | Drum conductivity via blowdown |
| FIC-0101 | FT-0102 | FCV-0101 | reverse | AUTO | - | 1.80 | 60 | D3 off-gas to fuel header |
| FIC-1001 | FT-1001 | FCV-1001 | reverse | CAS | LIC-1001 | 2.40 | 60 | Charge flow |
| FIC-1002 | FT-1003 | FCV-1002 | reverse | AUTO | - | 0.90 | 90 | Charge pump minimum flow |
| FIC-3001 | FT-3001 | FCV-3001 | reverse | CAS | TIC-3001 | 2.00 | 30 | H1 fuel gas flow |
| FIC-3003 | FT-3003 | FCV-3004 | reverse | CAS | TIC-3001 | 0.49 | 45 | H1 combustion air flow |
| FIC-4001 | FT-4001 | FCV-4001 | reverse | CAS | TIC-4001 | 0.24 | 20 | R1 quench gas flow |
| FIC-5001 | FT-5002 | FCV-5001 | reverse | CAS | TIC-5001 | 2.00 | 45 | T1 reflux flow |
| FIC-5002 | FT-5005 | FCV-5004 | reverse | CAS | TIC-5002 | 2.00 | 45 | T1 reboiler steam flow |
| FIC-5003 | FT-5006 | FCV-5005 | reverse | AUTO | - | 2.10 | 60 | T1 condenser cooling water |
| FIC-5004 | FT-5007 | FCV-5006 | reverse | AUTO | - | 1.80 | 90 | P-501 minimum flow protection |
| FIC-5005 | FT-5008 | FCV-5007 | reverse | AUTO | - | 1.50 | 90 | P-502 minimum flow protection |
| FIC-6001 | FT-6002 | FCV-6001 | reverse | CAS | TIC-6001 | 2.00 | 45 | T2 reflux flow |
| FIC-6002 | FT-6005 | FCV-6004 | reverse | CAS | TIC-6002 | 2.00 | 45 | T2 reboiler steam flow |
| FIC-6003 | FT-6006 | FCV-6005 | reverse | AUTO | - | 2.10 | 60 | T2 condenser cooling water |
| FIC-6004 | FT-6007 | FCV-6006 | reverse | AUTO | - | 1.80 | 90 | P-601 minimum flow protection |
| FIC-6005 | FT-6008 | FCV-6007 | reverse | AUTO | - | 1.50 | 90 | P-602 minimum flow protection |
| FIC-7001 | FT-7002 | FCV-7001 | reverse | CAS | LIC-7001 | 1.60 | 40 | B1 feedwater flow |
| FIC-7002 | FT-7003 | FCV-7002 | reverse | CAS | PIC-7001 | 2.00 | 30 | B1 fuel gas flow |
| FIC-7003 | FT-7004 | SC-7001 | reverse | CAS | PIC-7001 | 0.66 | 45 | B1 combustion air flow |
| FIC-8001 | FT-8001 | FCV-8004 | reverse | CAS | LIC-8001 | 1.80 | 60 | Effluent discharge flow |
| LIC-0101 | LT-0101 | LCV-0101 | reverse | AUTO | - | 1.50 | 600 | CT1 basin makeup water |
| LIC-1001 | LT-1001 | cascade SP | direct | AUTO | - | 1.60 | 700 | D1 level to charge flow |
| LIC-1002 | LT-1001 | LCV-1001 | reverse | AUTO | - | 1.00 | 900 | D1 level via off-spec import |
| LIC-2001 | LT-2001 | LCV-2001 | direct | AUTO | - | 2.00 | 300 | V-201 KO drum level |
| LIC-4001 | LT-4001 | LCV-4001 | direct | AUTO | - | 2.20 | 420 | D3 level to T1 |
| LIC-4002 | LT-4002 | LCV-4002 | direct | AUTO | - | 1.60 | 420 | D3 interface, sour water draw |
| LIC-5001 | LT-5001 | FCV-5002 | direct | AUTO | - | 3.00 | 320 | T1 reflux drum level to distillate |
| LIC-5002 | LT-5002 | FCV-5003 | direct | AUTO | - | 1.20 | 600 | T1 sump level |
| LIC-5003 | LT-5003 | LCV-5001 | direct | AUTO | - | 1.60 | 420 | T1 drum water boot |
| LIC-6001 | LT-6001 | FCV-6002 | direct | AUTO | - | 3.00 | 320 | T2 reflux drum level to distillate |
| LIC-6002 | LT-6002 | FCV-6003 | direct | AUTO | - | 1.20 | 600 | T2 sump level |
| LIC-7001 | LT-7001 | cascade SP | reverse | AUTO | - | 1.32 | 240 | B1 drum level, three element |
| LIC-7002 | LT-7002 | LCV-7001 | reverse | AUTO | - | 1.50 | 300 | Deaerator level |
| LIC-8001 | LT-8001 | cascade SP | direct | AUTO | - | 2.00 | 300 | Neutralisation tank level |
| PDIC-5001 | PDT-5001 | cascade SP | reverse | AUTO | - | 2.50 | 60 | override, parked at 100 |
| PDIC-6001 | PDT-6001 | cascade SP | reverse | AUTO | - | 2.50 | 60 | override, parked at 100 |
| PIC-0101 | PT-0101 | split: PCV-0102/PCV-0101 | reverse | AUTO | - | 1.50 | 240 | Fuel gas header pressure |
| PIC-0102 | PT-0102 | PCV-0103 | reverse | AUTO | - | 1.80 | 120 | Fuel gas to H1 |
| PIC-0103 | PT-0103 | PCV-0104 | reverse | AUTO | - | 1.20 | 150 | Fuel gas to B1 |
| PIC-0105 | PT-0105 | SC-0101 | reverse | AUTO | - | 4.00 | 30 | Cooling-water supply-header pressure |
| PIC-1001 | PT-1002 | cascade SP | reverse | AUTO | - | 0.60 | 180 | override, parked at 100 |
| PIC-2001 | PT-2001 | cascade SP | direct | AUTO | - | 1.00 | 200 | C1 suction pressure to speed |
| PIC-2002 | PT-2002 | cascade SP | direct | AUTO | - | 1.20 | 120 | override, parked at 0 |
| PIC-3001 | PT-3002 | split: FCV-3002/FCV-3003 | direct | CAS | ZC-3001 | 1.00 | 200 | H1 fuel oil header pressure |
| PIC-3002 | PT-3003 | SC-3001 | direct | AUTO | - | 0.90 | 120 | H1 draft to ID fan |
| PIC-4001 | PT-4002 | PCV-4001 | direct | AUTO | - | 1.65 | 320 | D3 pressure |
| PIC-5001 | PT-5001 | split: PCV-5002/PCV-5001 | direct | AUTO | - | 1.80 | 120 | T1 pressure, vent and hot gas bypass |
| PIC-6001 | PT-6001 | PCV-6001 | direct | AUTO | - | 0.60 | 260 | T2 pressure |
| PIC-7001 | PT-7002 | cascade SP | reverse | AUTO | - | 1.12 | 200 | MP steam header pressure, boiler master |
| PIC-7002 | PT-7002 | PCV-7001 | direct | AUTO | - | 1.35 | 120 | override, parked at 0 |
| SIC-1001 | ST-1001 | SC-1001 | reverse | AUTO | - | 2.00 | 20 | P-101A VFD speed control |
| SIC-2001 | ST-2001 | SC-2001 | reverse | CAS | PIC-2001 | 2.10 | 15 | C1 VFD speed control |
| TIC-0102 | TT-0102 | SC-0102 | direct | AUTO | - | 3.00 | 180 | CT1 supply temperature via common fan speed |
| TIC-2001 | TT-2002 | cascade SP | direct | AUTO | - | 3.30 | 120 | override, parked at 0 |
| TIC-2002 | TT-2002 | FCV-2003 | direct | AUTO | - | 3.30 | 180 | C1 discharge temperature |
| TIC-3001 | TT-3001 | cascade SP | reverse | AUTO | - | 6.00 | 300 | H1 outlet temperature |
| TIC-3002 | TT-3002 | FCV-3005 | direct | AUTO | - | 5.00 | 300 | H1 pass 1 outlet, pass balancing |
| TIC-3003 | TT-3003 | FCV-3006 | direct | AUTO | - | 5.00 | 300 | H1 pass 2 outlet, pass balancing |
| TIC-3004 | TT-3005 | cascade SP | reverse | AUTO | - | 15.00 | 120 | override, parked at 100; PV = high select of TT-3005 / TT-3006 |
| TIC-4001 | TT-4002 | cascade SP | direct | CAS | AIC-4001 | 5.50 | 320 | PV = high select of TT-4002 / TT-4003 |
| TIC-4002 | TT-4002 | cascade SP | direct | AUTO | - | 16.50 | 60 | override, parked at 0; PV = high select of TT-4002 / TT-4003 |
| TIC-4005 | TT-4005 | FCV-4002 | direct | AUTO | - | 2.40 | 240 | D3 temperature via E-401 CW |
| TIC-5001 | TT-5003 | cascade SP | direct | CAS | AIC-5001 | 1.05 | 1500 | pressure-compensated SP on PT-5001 |
| TIC-5002 | TT-5006 | cascade SP | reverse | CAS | AIC-5002 | 1.20 | 1500 | pressure-compensated SP on PT-5002 |
| TIC-6001 | TT-6003 | cascade SP | direct | CAS | AIC-6001 | 1.05 | 1500 | pressure-compensated SP on PT-6001 |
| TIC-6002 | TT-6006 | cascade SP | reverse | CAS | AIC-6002 | 1.20 | 1500 | pressure-compensated SP on PT-6002 |
| TIC-7001 | TT-7001 | TCV-7001 | direct | AUTO | - | 4.50 | 200 | Superheat via desuperheater spray |
| UIC-2001 | UY-2001 | FCV-2001 | reverse | AUTO | - | 2.40 | 120 | C1 anti-surge margin |
| ZC-3001 | ZT-3002 | cascade SP | direct | AUTO | - | 0.20 | 900 | FCV-3002 valve position controller |

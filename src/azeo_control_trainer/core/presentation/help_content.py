"""Comprehensive process reference HTML for the Help dialog.

Contains detailed theory extracted from simulation source code:
ODE model, heat transfer, combustion, controls, step test, etc.
"""

HELP_HTML = """
<style>
body { font-family: Segoe UI, Arial, sans-serif; background: #F5F5F5; }
h1 { color: #1A1A1A; border-bottom: 2px solid #4169E1; padding-bottom: 6px; }
h2 { color: #333; margin-top: 28px; border-bottom: 1px solid #CCC;
     padding-bottom: 4px; }
h3 { color: #4169E1; margin-top: 16px; }
h4 { color: #505050; margin-top: 12px; }
table { border-collapse: collapse; margin: 8px 0; width: 100%; }
th, td { border: 1px solid #CCC; padding: 4px 8px; text-align: left; }
th { background: #E0E0E0; }
code { background: #E8E8E8; padding: 1px 4px; border-radius: 3px;
       font-family: Consolas, monospace; }
pre { background: #E8E8E8; padding: 8px 12px; border-radius: 4px;
      font-family: Consolas, monospace; font-size: 9pt; overflow-x: auto;
      border: 1px solid #CCC; }
.eq { background: #F0F0F0; border: 1px solid #DDD; padding: 6px 12px;
      border-radius: 4px; margin: 6px 0; font-family: Consolas, monospace;
      font-size: 9.5pt; }
.note { background: #FFFDE7; border-left: 3px solid #DAA520; padding: 6px 10px;
        margin: 6px 0; font-size: 9.5pt; }
.new { background: #E8F5E9; border-left: 3px solid #2E8B2E; padding: 6px 10px;
       margin: 6px 0; font-size: 9.5pt; }
</style>

<h1>Fired Heater Simulator &mdash; Process Reference</h1>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>1. Process Overview</h2>

<h3>Fired Heater Configuration</h3>
<ul>
<li><b>Type:</b> 4-pass cabin-type fired heater for crude preheating</li>
<li><b>Capacity:</b> ~100,000 BPD crude charge</li>
<li><b>Sections:</b> Convection section (upper) + Radiant section (lower firebox)</li>
<li><b>Firebox:</b> 2-zone model (burner zone + bridgewall zone)</li>
<li><b>Model:</b> 27-state ODE system with 0.1 s integration time step</li>
<li><b>Integrator:</b> RK45 (Runge-Kutta 4th/5th order), rtol=1e-6, atol=1e-8</li>
<li><b>Control:</b> 12 PID loops with cascade, cross-limiting, and ISA-18.2 alarms</li>
</ul>

<h3>27 State Variables</h3>
<table>
<tr><th>Index</th><th>Symbol</th><th>Description</th><th>Units</th></tr>
<tr><td>0&ndash;7</td><td>T_fluid_1..4, T_metal_1..4</td><td>Pass 1&ndash;4 fluid outlet &amp; tube metal temps</td><td>K</td></tr>
<tr><td>8</td><td>T_gas (Zone 1)</td><td>Firebox burner zone gas temperature</td><td>K</td></tr>
<tr><td>9</td><td>T_flue_out</td><td>Stack / flue gas exit temperature</td><td>K</td></tr>
<tr><td>10</td><td>T_conv_out</td><td>Crude leaving convection section</td><td>K</td></tr>
<tr><td>11</td><td>P_draft</td><td>Firebox draft pressure</td><td>Pa</td></tr>
<tr><td>12&ndash;15</td><td>valve_feed, valve_fuel,<br/>valve_air, valve_damper</td><td>Main control valve positions</td><td>0&ndash;1</td></tr>
<tr><td>16&ndash;22</td><td>m_holdup_1..3,<br/>valve_pass_1..4</td><td>Fluid holdups + per-pass valve positions</td><td>kg / 0&ndash;1</td></tr>
<tr><td>23</td><td>P_fuel_gas</td><td>Fuel gas burner header pressure</td><td>Pa</td></tr>
<tr><td>24</td><td>T_gas_zone2</td><td>Firebox bridgewall zone gas temperature</td><td>K</td></tr>
<tr><td>25</td><td>T_refr_intermediate</td><td>Refractory intermediate layer temperature</td><td>K</td></tr>
<tr><td>26</td><td>T_refr_cold</td><td>Refractory cold face temperature</td><td>K</td></tr>
</table>

<h3>Crude Oil Properties (API 32, Mid-Continent)</h3>
<table>
<tr><th>Property</th><th>Correlation</th><th>Range</th></tr>
<tr><td>Density &rho;</td><td><code>&rho;(T) = 903.0 - 0.365 &times; (T - 288.15)</code> kg/m&sup3;</td><td>~850 @ 288K, ~700 @ 633K</td></tr>
<tr><td>Specific heat C<sub>p</sub></td><td><code>C<sub>p</sub>(T) = 1680 + 1.88 &times; (T - 273.15)</code> J/(kg&middot;K)</td><td>~1730 @ 300K, ~2360 @ 633K</td></tr>
<tr><td>Viscosity &mu;</td><td><code>&mu;(T) = 0.015 &times; exp(-0.012 &times; (T - 273.15))</code> Pa&middot;s</td><td>~5 cP @ 373K, ~0.3 cP @ 633K</td></tr>
<tr><td>Thermal cond. k</td><td><code>k(T) = 0.145 - 1.3e-4 &times; (T - 273.15)</code> W/(m&middot;K)</td><td>~0.13 @ 300K, ~0.10 @ 633K</td></tr>
</table>

<h3>Two-Phase VLE Properties</h3>
<div class="new"><b>NEW:</b> Two-phase vapor-liquid equilibrium with sigmoid flash model.</div>
<table>
<tr><th>Property</th><th>Model</th></tr>
<tr><td>Vapor fraction x(T)</td><td><code>1 / (1 + exp(-k &times; (T - T_mid)))</code>, T_bubble=573 K, T_dew=693 K</td></tr>
<tr><td>Latent heat</td><td>230 kJ/kg</td></tr>
<tr><td>Effective C<sub>p</sub></td><td><code>Cp_l&times;(1-x) + Cp_v&times;x + LATENT&times;dx/dT</code> (includes latent heat hump)</td></tr>
<tr><td>Effective density</td><td><code>1/&rho; = x/&rho;_v + (1-x)/&rho;_l</code> (volume-averaged)</td></tr>
<tr><td>Effective viscosity</td><td>McAdams: <code>1/&mu; = x/&mu;_v + (1-x)/&mu;_l</code></td></tr>
<tr><td>Boiling HTC</td><td>Chen-type multiplier with dryout suppression at x &gt; 0.88</td></tr>
<tr><td>Thermal cracking</td><td>Smooth onset above 700 K (endothermic reaction)</td></tr>
</table>

<h3>Design Operating Point</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Coil Outlet Temp (COT)</td><td>680 &deg;F (633 K)</td></tr>
<tr><td>Firebox Zone 1 (Burner)</td><td>~1500 &deg;F (1089 K)</td></tr>
<tr><td>Firebox Zone 2 (Bridgewall)</td><td>~1350 &deg;F (1005 K)</td></tr>
<tr><td>Stack Temperature</td><td>~650 &deg;F (616 K)</td></tr>
<tr><td>Draft Pressure</td><td>-0.30 inH2O (-74.7 Pa)</td></tr>
<tr><td>Feed Flow</td><td>~100,000 BPD (1.688 kg/s)</td></tr>
<tr><td>Excess Air</td><td>15%</td></tr>
<tr><td>Stack O2</td><td>3.0% (dry basis)</td></tr>
<tr><td>CO</td><td>&lt; 50 ppm</td></tr>
<tr><td>NOx</td><td>30&ndash;150 ppm (thermal Zeldovich)</td></tr>
<tr><td>Fired Duty</td><td>41.1 MW absorbed</td></tr>
<tr><td>Thermal Efficiency</td><td>~85%</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>2. Radiant Section &mdash; Multi-Zone Lobo-Evans Model</h2>

<div class="new"><b>NEW:</b> 2-zone firebox, soot radiation, temperature-dependent tube emissivity, view factors.</div>

<h3>Firebox Geometry</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Firebox dimensions</td><td>20.0 m (L) &times; 8.0 m (W) &times; 14.0 m (H)</td></tr>
<tr><td>Firebox volume</td><td>2240 m&sup3;</td></tr>
<tr><td>Zone 1 (burner, lower)</td><td>50% of volume, tube passes 0&ndash;1</td></tr>
<tr><td>Zone 2 (bridgewall, upper)</td><td>50% of volume, tube passes 2&ndash;3</td></tr>
<tr><td>Zone interface area</td><td>L &times; W = 160 m&sup2;</td></tr>
<tr><td>Tubes per pass</td><td>30 (serpentine coil, 4 passes &times; 30 = 120 total)</td></tr>
<tr><td>Tube OD / ID</td><td>0.1143 m (4.5 in) / 0.0979 m</td></tr>
<tr><td>Tube pitch</td><td>2.0 &times; OD (double-sided firing)</td></tr>
<tr><td>Tube length (radiant)</td><td>20.0 m per tube</td></tr>
<tr><td>Tube material</td><td>5Cr-0.5Mo steel (&rho;=7850, C<sub>p</sub>=500, k=35)</td></tr>
</table>

<h3>Per-Zone Mean Beam Length</h3>
<div class="eq">L<sub>beam,zone</sub> = 3.6 &times; V<sub>zone</sub> / A<sub>zone</sub></div>
<p>Computed separately for each zone using zone-specific volume and enclosure area.</p>

<h3>Gas Emissivity (Hottel with Actual Partial Pressures)</h3>
<div class="new"><b>NEW:</b> Uses actual CO2 and H2O partial pressures from flue gas composition.</div>
<div class="eq">
P<sub>L</sub> = (x<sub>CO2</sub> + x<sub>H2O</sub>) &times; 1.0 atm &nbsp;&nbsp; (from combustion products)<br/>
optical_thickness = K &times; P<sub>L</sub> &times; L<sub>beam,zone</sub><br/>
&epsilon;<sub>g</sub>(T) = C<sub>1</sub> &times; (1 - exp(-optical_thickness)) &times; (T<sub>ref</sub> / T)<sup>0.45</sup>
</div>
<table>
<tr><th>Constant</th><th>Value</th><th>Description</th></tr>
<tr><td>C<sub>1</sub></td><td>0.85</td><td>Maximum emissivity constant</td></tr>
<tr><td>K</td><td>0.65 (atm&middot;m)<sup>-1</sup></td><td>Gas absorption coefficient</td></tr>
<tr><td>T<sub>ref</sub></td><td>1000 K</td><td>Reference temperature</td></tr>
</table>

<h3>Soot Radiation (Mie Scattering)</h3>
<div class="new"><b>NEW:</b> Soot contribution to gas emissivity.</div>
<div class="eq">
&epsilon;<sub>soot</sub> = 1 - exp(-K<sub>soot</sub> &times; C<sub>soot</sub> &times; L<sub>beam</sub>)<br/>
&epsilon;<sub>total</sub> = &epsilon;<sub>gas</sub> + &epsilon;<sub>soot</sub> - &epsilon;<sub>gas</sub> &times; &epsilon;<sub>soot</sub>
</div>
<p>K<sub>soot</sub> = 1264 m<sup>-1</sup> (absorption coefficient), C<sub>soot</sub> = 0.01 g/m<sup>3</sup> (default). Combined emissivity accounts for overlap.</p>

<h3>Temperature-Dependent Tube Emissivity</h3>
<div class="new"><b>NEW:</b> Varies with tube metal temperature.</div>
<div class="eq">
&epsilon;<sub>tube</sub>(T) = 0.82 + 1.5 &times; 10<sup>-4</sup> &times; (T<sub>metal</sub> - 500)
</div>
<p>Range: 0.82 at 500 K to ~0.95 at hot end. Accounts for increased oxidation at higher temperatures.</p>

<h3>View Factor (Tubes to Zone)</h3>
<div class="eq">
F = &epsilon;<sub>tube</sub> &times; A<sub>tube</sub> / (A<sub>zone</sub> &times; (1 - (1 - &epsilon;<sub>tube</sub>) &times; A<sub>tube</sub>/A<sub>zone</sub>))
</div>
<p>Gebhart simplified model accounting for tube geometry and refractory re-radiation.</p>

<h3>Zoned Radiant Heat Transfer</h3>
<div class="eq">
Q<sub>rad,zone</sub> = &sigma; &times; F<sub>zone</sub> &times; (T<sub>gas,zone</sub><sup>4</sup> - T<sub>tube,avg,zone</sub><sup>4</sup>)
</div>
<p>Computed separately for each zone using zone gas temperature and zone tube metal temperatures.</p>

<h3>Inter-Zone Radiation</h3>
<div class="new"><b>NEW:</b> Net radiative exchange between zones.</div>
<div class="eq">
Q<sub>interzone</sub> = &sigma; &times; F<sub>view</sub> &times; A<sub>interface</sub> &times; (T<sub>z1</sub><sup>4</sup> - T<sub>z2</sub><sup>4</sup>)
</div>
<p>F<sub>view</sub> = 0.6 (large parallel plane approximation). Heat flows from hot burner zone to cooler bridgewall zone.</p>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>3. Firebox Model &mdash; 2-Zone with 3-Layer Refractory</h2>

<div class="new"><b>NEW:</b> Multi-zone gas model with 3-layer refractory thermal mass and ambient losses.</div>

<h3>3-Layer Refractory Model</h3>
<table>
<tr><th>Layer</th><th>Thickness</th><th>Mass Fraction</th><th>Time Constant</th><th>Description</th></tr>
<tr><td>Hot face (castable)</td><td>3 mm</td><td>3%</td><td>~5 s</td><td>Fast response to firing changes</td></tr>
<tr><td>Intermediate (insulating brick)</td><td>20 mm</td><td>20%</td><td>~120 s</td><td>Medium-term thermal storage</td></tr>
<tr><td>Cold face (steel shell)</td><td>75 mm</td><td>77%</td><td>~3600 s</td><td>Slow bulk heat storage</td></tr>
</table>

<h3>Zone Energy Balances</h3>
<div class="eq">
<b>Zone 1 (Burner, lower):</b><br/>
C<sub>z1</sub> &times; dT<sub>z1</sub>/dt = Q<sub>release</sub> - Q<sub>rad,z1</sub> - Q<sub>interzone</sub> - Q<sub>ambient,z1</sub> - Q<sub>flue,z1&rarr;z2</sub><br/><br/>
<b>Zone 2 (Bridgewall, upper):</b><br/>
C<sub>z2</sub> &times; dT<sub>z2</sub>/dt = Q<sub>flue,z1&rarr;z2</sub> - Q<sub>rad,z2</sub> + Q<sub>interzone</sub> - Q<sub>ambient,z2</sub> - Q<sub>flue,exit</sub>
</div>
<p>All heat release enters Zone 1 (burner zone). Gas rises from Zone 1 to Zone 2 and exits to convection.</p>

<h3>Refractory Layer ODEs</h3>
<div class="eq">
dT<sub>refr,intermediate</sub>/dt = (T<sub>gas,avg</sub> - T<sub>refr,intermediate</sub>) / &tau;<sub>intermediate</sub><br/>
dT<sub>refr,cold</sub>/dt = (T<sub>refr,intermediate</sub> - T<sub>refr,cold</sub>) / &tau;<sub>cold</sub>
</div>
<p>Three time scales capture realistic refractory thermal behavior: fast skin (~5s), intermediate (~2 min), and bulk (~1 hr).</p>

<h3>Ambient Heat Losses</h3>
<div class="eq">
Q<sub>ambient</sub> = h<sub>amb</sub> &times; A<sub>wall</sub> &times; (T<sub>cold_face</sub> - T<sub>ambient</sub>)
</div>
<p>h<sub>amb</sub> = 5 W/(m&sup2;&middot;K) (natural convection + radiation to surroundings).</p>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>4. Convection Section</h2>

<h3>Geometry</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Tubes per row</td><td>24</td></tr>
<tr><td>Number of rows</td><td>10</td></tr>
<tr><td>Tube OD / ID</td><td>0.0889 m (3.5 in) / 0.0779 m</td></tr>
<tr><td>Tube length</td><td>8.0 m</td></tr>
<tr><td>Fin density</td><td>197 fins/m (5 fins/inch)</td></tr>
<tr><td>Fin height / thickness</td><td>0.0127 m / 0.001 m</td></tr>
<tr><td>Extended area ratio</td><td>5.0 &times; bare area</td></tr>
</table>

<h3>Gas-Side HTC (Zukauskas Correlation)</h3>
<div class="new"><b>NEW:</b> Replaces constant h=50 with Re-dependent correlation.</div>
<div class="eq">
Nu = C &times; Re<sup>m</sup> &times; Pr<sup>0.36</sup> &times; (Pr / Pr<sub>w</sub>)<sup>0.25</sup><br/>
h<sub>gas</sub> = Nu &times; k<sub>flue</sub> / D<sub>o</sub>
</div>
<p>C, m from standard tube bank tables for staggered arrangement. Typical result: 30&ndash;80 W/(m&sup2;&middot;K) depending on flue gas velocity and temperature.</p>

<h3>Fin Efficiency (ESCOA Method)</h3>
<div class="new"><b>NEW:</b> Accounts for fin heat transfer effectiveness.</div>
<div class="eq">
&eta;<sub>fin</sub> = tanh(m &times; H) / (m &times; H)<br/>
where m = &radic;(2 &times; h<sub>gas</sub> / (k<sub>fin</sub> &times; t<sub>fin</sub>))
</div>
<p>Typical efficiency: 0.5&ndash;0.95. Lower h<sub>gas</sub> &rarr; higher efficiency. Published to store as <code>fin_efficiency</code>.</p>

<h3>Overall Heat Transfer Coefficient</h3>
<div class="eq">
1/U<sub>o</sub> = 1/(h<sub>gas</sub> &times; &eta;<sub>fin</sub>) + (A<sub>o</sub>/A<sub>i</sub>)/h<sub>i</sub> + R<sub>fouling</sub>(t) &times; (A<sub>o</sub>/A<sub>i</sub>) + R<sub>wall</sub>
</div>
<table>
<tr><th>Term</th><th>Value</th><th>Description</th></tr>
<tr><td>h<sub>gas</sub> &times; &eta;<sub>fin</sub></td><td>Computed (Zukauskas + ESCOA)</td><td>Effective gas-side coefficient</td></tr>
<tr><td>h<sub>i</sub></td><td>Dittus-Boelter (or two-phase Chen)</td><td>Crude inside film coefficient</td></tr>
<tr><td>R<sub>fouling</sub>(t)</td><td>Dynamic (T-dependent)</td><td>Crude-side fouling resistance</td></tr>
<tr><td>R<sub>wall</sub></td><td>(OD/2) &times; ln(OD/ID) / k<sub>steel</sub></td><td>Cylindrical wall conduction</td></tr>
</table>

<h3>Dynamic Fouling Model</h3>
<div class="new"><b>NEW:</b> Temperature-dependent fouling accumulation.</div>
<div class="eq">
R(t) = R<sub>initial</sub> + k<sub>foul</sub> &times; max(0, T<sub>skin</sub> - T<sub>threshold</sub>)<sup>n</sup> &times; t
</div>
<p>Fouling rate increases with tube skin temperature above threshold. Approaches asymptotic limit. Published to store as <code>fouling_R_current</code>.</p>

<h3>LMTD (Counter-Current)</h3>
<div class="eq">
&Delta;T<sub>1</sub> = T<sub>flue,in</sub> - T<sub>crude,out</sub> &nbsp;&nbsp; (hot end)<br/>
&Delta;T<sub>2</sub> = T<sub>flue,out</sub> - T<sub>crude,in</sub> &nbsp;&nbsp; (cold end)<br/>
LMTD = (&Delta;T<sub>1</sub> - &Delta;T<sub>2</sub>) / ln(&Delta;T<sub>1</sub> / &Delta;T<sub>2</sub>)
</div>

<h3>Convection Duty</h3>
<div class="eq">Q<sub>conv</sub> = U &times; A<sub>eff</sub> &times; LMTD &nbsp; [W]</div>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>5. Combustion Chemistry</h2>

<h3>Stoichiometric Reactions</h3>
<table>
<tr><th>Reaction</th><th>O<sub>2</sub> Required</th></tr>
<tr><td>CH<sub>4</sub> + 2 O<sub>2</sub> &rarr; CO<sub>2</sub> + 2 H<sub>2</sub>O</td><td>2.0 mol</td></tr>
<tr><td>C<sub>2</sub>H<sub>6</sub> + 3.5 O<sub>2</sub> &rarr; 2 CO<sub>2</sub> + 3 H<sub>2</sub>O</td><td>3.5 mol</td></tr>
<tr><td>C<sub>3</sub>H<sub>8</sub> + 5 O<sub>2</sub> &rarr; 3 CO<sub>2</sub> + 4 H<sub>2</sub>O</td><td>5.0 mol</td></tr>
<tr><td>H<sub>2</sub> + 0.5 O<sub>2</sub> &rarr; H<sub>2</sub>O</td><td>0.5 mol</td></tr>
<tr><td>N<sub>2</sub> &mdash; inert</td><td>0</td></tr>
</table>

<h3>Design Fuel Composition (mol fractions)</h3>
<table>
<tr><th>CH<sub>4</sub></th><th>C<sub>2</sub>H<sub>6</sub></th><th>C<sub>3</sub>H<sub>8</sub></th><th>H<sub>2</sub></th><th>N<sub>2</sub></th></tr>
<tr><td>0.70</td><td>0.15</td><td>0.05</td><td>0.08</td><td>0.02</td></tr>
</table>

<h3>Adiabatic Flame Temperature</h3>
<div class="new"><b>NEW:</b> Iterative energy balance with composition-dependent Cp.</div>
<div class="eq">
T<sub>flame</sub> such that: &Sigma;(n<sub>i</sub> &times; C<sub>p,i</sub> &times; (T<sub>flame</sub> - T<sub>air</sub>)) = Q<sub>release</sub>
</div>
<p>Accounts for excess air dilution. Typical result: 1800&ndash;2200 K depending on excess air. Published as <code>T_flame</code>.</p>

<h3>Thermal NOx (Zeldovich Mechanism)</h3>
<div class="new"><b>NEW:</b> First-principles NOx prediction.</div>
<div class="eq">
rate = A &times; [N<sub>2</sub>] &times; [O<sub>2</sub>]<sup>0.5</sup> &times; exp(-E<sub>a</sub> / (R &times; T<sub>eff</sub>))<br/>
NOx<sub>ppm</sub> = rate &times; &tau;<sub>residence</sub>
</div>
<table>
<tr><th>Constant</th><th>Value</th><th>Description</th></tr>
<tr><td>A</td><td>1.8 &times; 10<sup>8</sup></td><td>Pre-exponential factor</td></tr>
<tr><td>E<sub>a</sub>/R</td><td>38370 K</td><td>Activation energy / gas constant</td></tr>
<tr><td>T<sub>eff</sub></td><td>0.7 &times; T<sub>flame</sub> + 0.3 &times; T<sub>gas</sub></td><td>Weighted effective temperature</td></tr>
</table>
<p>Typical result: 30&ndash;150 ppm. Strongly dependent on flame temperature and residence time.</p>

<h3>CO Model (Kinetic)</h3>
<div class="new"><b>NEW:</b> Replaces pure empirical model with kinetic approach.</div>
<div class="eq">
CO/CO<sub>2</sub> equilibrium + kinetic burnout rate<br/>
Accounts for temperature, excess air, and residence time &tau;
</div>
<p>Published as <code>CO_ppm</code>. More accurate than the previous empirical <code>15 &times; exp(-12 &times; excess)</code>.</p>
<div class="note">
<b>Safety thresholds:</b> Alarm 200 ppm | Override 500 ppm | Trip 1000 ppm
</div>

<h3>SO2 Tracking</h3>
<div class="new"><b>NEW:</b> Mass balance from fuel sulfur.</div>
<div class="eq">
SO2<sub>ppm</sub> = (S<sub>fuel</sub> &times; m&#775;<sub>fuel</sub> &times; MW<sub>SO2</sub>) / (MW<sub>S</sub> &times; m&#775;<sub>flue</sub>) &times; 10<sup>6</sup>
</div>
<p>FUEL_SULFUR_CONTENT = 0.0005 (50 ppm typical refinery fuel gas). Published as <code>SO2_ppm</code>.</p>

<h3>Residence Time</h3>
<div class="eq">
&tau;<sub>residence</sub> = &rho;<sub>gas</sub> &times; V<sub>firebox</sub> / m&#775;<sub>flue</sub>
</div>
<p>Published as <code>tau_residence</code>. Typically 0.5&ndash;2.0 s for a cabin-type heater.</p>

<h3>Flue Gas Specific Heat (JANAF-style, 300&ndash;1500 K)</h3>
<div class="eq">
T' = T / 1000 &nbsp; [kK]<br/>
C<sub>p,N2</sub> = 29.0 + 1.5 &times; T' &nbsp; J/(mol&middot;K)<br/>
C<sub>p,CO2</sub> = 37.0 + 17.0 &times; T'<br/>
C<sub>p,H2O</sub> = 33.5 + 5.0 &times; T'<br/>
C<sub>p,O2</sub> = 29.4 + 4.0 &times; T'
</div>

<h3>Fuel Gas Properties</h3>
<div class="eq">
SG = MW<sub>fuel</sub> / MW<sub>air</sub> &nbsp;&nbsp; (MW<sub>air</sub> = 28.965)<br/>
NHV<sub>vol</sub> = &Sigma;(y<sub>i</sub> &times; NHV<sub>i</sub>) / 22.414 &nbsp; [MJ/Nm&sup3;]<br/>
Wobbe Index = NHV<sub>vol</sub> / &radic;SG &nbsp; [MJ/Nm&sup3;]
</div>

<h3>Thermal Efficiency &amp; Energy Balance</h3>
<div class="eq">
&eta; = (Q<sub>rad,total</sub> + Q<sub>conv</sub>) / Q<sub>release</sub> &times; 100%
</div>
<div class="new"><b>NEW:</b> Energy conservation tracking: <code>energy_balance_error</code> published as
<code>(Q_in - Q_out - dE_stored) / Q_in &times; 100%</code>. Verified &lt; 1% at steady state.</div>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>6. Pressure &amp; Draft Model</h2>

<h3>Stack Geometry</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Stack height</td><td>45.0 m</td></tr>
<tr><td>Stack diameter</td><td>3.5 m</td></tr>
<tr><td>Stack cross-section</td><td>&pi;/4 &times; 3.5&sup2; &asymp; 9.62 m&sup2;</td></tr>
</table>

<h3>Natural Draft (Buoyancy)</h3>
<div class="eq">P<sub>natural</sub> = -g &times; H<sub>stack</sub> &times; (&rho;<sub>ambient</sub> - &rho;<sub>hot</sub>) &nbsp; [Pa, negative = vacuum]</div>

<h3>Draft ODE</h3>
<div class="eq">
&tau;<sub>draft</sub> &times; dP/dt = P<sub>steady</sub> - P<sub>draft</sub> &nbsp;&nbsp;&nbsp; (&tau; = 2.0 s)<br/>
P<sub>steady</sub> = P<sub>natural</sub> &times; f<sub>damper</sub> + &Delta;P<sub>friction</sub>
</div>

<h3>Tube-Side Pressure Drop</h3>
<div class="new"><b>NEW:</b> Detailed tube-side &Delta;P with Churchill friction factor.</div>
<div class="eq">
<b>Churchill friction factor</b> (all-regime, no discontinuities):<br/>
f = 8 &times; [(8/Re)<sup>12</sup> + 1/(A+B)<sup>3/2</sup>]<sup>1/12</sup><br/><br/>
<b>Return bend losses:</b> K = 1.5 per bend, 59 bends per pass<br/>
<b>Two-phase multiplier (Lockhart-Martinelli):</b><br/>
&phi;&sup2; = 1 + C/X<sub>tt</sub> + 1/X<sub>tt</sub>&sup2; &nbsp;&nbsp; (C=20 for turbulent)
</div>
<p>Published per-pass as <code>tube_dp_rad_1..4</code>, convection as <code>tube_dp_conv</code>, total as <code>tube_dp_total</code>.</p>

<h3>Gas-Side Pressure Drop</h3>
<div class="new"><b>NEW:</b> Convection bank, stack, and damper &Delta;P.</div>
<table>
<tr><th>Component</th><th>Model</th><th>Tag</th></tr>
<tr><td>Convection bank</td><td>Zukauskas for staggered finned bank</td><td><code>gas_dp_conv_bank</code></td></tr>
<tr><td>Stack friction</td><td>Darcy-Weisbach: f<sub>D</sub> &times; H/D &times; &frac12;&rho;v&sup2;</td><td><code>gas_dp_stack_friction</code></td></tr>
<tr><td>Natural draft</td><td>Buoyancy: g &times; H &times; (&rho;<sub>amb</sub> - &rho;<sub>hot</sub>)</td><td><code>gas_dp_natural</code></td></tr>
<tr><td>Damper loss</td><td>Proportional to damper position</td><td>&mdash;</td></tr>
<tr><td>Net available</td><td>Natural - friction - bank - damper</td><td><code>gas_dp_net_available</code></td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>7. Valve &amp; Actuator Models</h2>

<h3>First-Order Actuator Lag</h3>
<div class="eq">&tau; &times; dx/dt = x<sub>target</sub> - x</div>
<table>
<tr><th>Valve</th><th>&tau; (s)</th><th>Description</th></tr>
<tr><td>Feed valve</td><td>3.0</td><td>Main feed flow</td></tr>
<tr><td>Fuel valve</td><td>2.0</td><td>Smaller, faster</td></tr>
<tr><td>Air valve</td><td>5.0</td><td>Larger, slower</td></tr>
<tr><td>Stack damper</td><td>4.0</td><td>Draft control</td></tr>
<tr><td>Per-pass valves</td><td>3.0</td><td>Pass flow balancing</td></tr>
</table>

<h3>Cv-Based Valve Sizing (ISA/IEC)</h3>
<div class="new"><b>NEW:</b> Proper ISA/IEC valve flow equation.</div>
<div class="eq">
Q = N<sub>1</sub> &times; C<sub>v</sub> &times; f(x) &times; &radic;(&Delta;P / &rho;)
</div>
<table>
<tr><th>Parameter</th><th>Description</th></tr>
<tr><td>C<sub>v</sub></td><td>Valve flow coefficient (US gallons/min at 1 psi &Delta;P, SG=1)</td></tr>
<tr><td>f(x)</td><td>Inherent characteristic: linear or equal-percentage</td></tr>
<tr><td>C<sub>f</sub></td><td>Critical flow factor for choked flow limit</td></tr>
<tr><td>Seal leakage</td><td>0.01% of max C<sub>v</sub> at valve_pos &lt; 2%</td></tr>
</table>

<h3>Installed Valve Characteristic</h3>
<div class="new"><b>NEW:</b> Accounts for system &Delta;P vs. valve &Delta;P ratio.</div>
<div class="eq">
f<sub>installed</sub> = f<sub>inherent</sub> / &radic;(r + (1 - r) &times; f<sub>inherent</sub>&sup2;)
</div>
<p>where r = valve &Delta;P / total system &Delta;P. At r=1: installed = inherent. As r &rarr; 0: equal-% becomes more linear.</p>

<h3>Valve Positioner Model</h3>
<div class="new"><b>NEW:</b> Inner proportional control loop.</div>
<div class="eq">
Positioner: P-only controller, response time ~0.5 s<br/>
command &rarr; position feedback &rarr; correction<br/>
Creates 2nd-order dynamics: positioner + actuator
</div>

<h3>Friction Model (Karnopp)</h3>
<div class="new"><b>NEW:</b> Realistic stick-slip behavior.</div>
<table>
<tr><th>Parameter</th><th>Description</th></tr>
<tr><td>Static friction (F<sub>s</sub>)</td><td>Breakaway force required to start motion</td></tr>
<tr><td>Coulomb friction (F<sub>c</sub>)</td><td>Constant kinetic friction during motion (F<sub>c</sub> &lt; F<sub>s</sub>)</td></tr>
<tr><td>Viscous friction (F<sub>v</sub>)</td><td>Friction proportional to velocity</td></tr>
<tr><td>Velocity threshold</td><td>Transition speed for stick/slip detection</td></tr>
</table>
<p>Produces realistic limit cycling behavior. Configurable on Process Models tab.</p>

<h3>Split-Range Configuration</h3>
<div class="new"><b>NEW:</b> Maps single controller output to two valve signals.</div>
<div class="eq">
Low valve range: [0.0, 0.5] &nbsp;&rarr;&nbsp; valve_low = 0&ndash;100%<br/>
High valve range: [0.5, 1.0] &nbsp;&rarr;&nbsp; valve_high = 0&ndash;100%
</div>

<h3>Valve Diagnostics</h3>
<div class="new"><b>NEW:</b> Maintenance-grade valve health tracking.</div>
<table>
<tr><th>Metric</th><th>Description</th><th>Tag</th></tr>
<tr><td>Total travel</td><td>Cumulative valve stem travel</td><td><code>valve_total_travel</code></td></tr>
<tr><td>Reversal count</td><td>Number of direction changes</td><td><code>valve_reversals</code></td></tr>
<tr><td>Time at saturation</td><td>Time spent at 0% or 100%</td><td><code>valve_time_saturated</code></td></tr>
<tr><td>Average velocity</td><td>Mean stem movement speed</td><td><code>valve_avg_velocity</code></td></tr>
</table>

<h3>Legacy Valve Nonlinearities (Optional)</h3>
<table>
<tr><th>Effect</th><th>Model</th><th>Default</th></tr>
<tr><td>Deadband</td><td>Ignore demand changes &lt; threshold</td><td>0%</td></tr>
<tr><td>Stiction</td><td>Valve sticks until &Delta;demand &gt; breakaway</td><td>0%</td></tr>
<tr><td>Hysteresis</td><td>Position offset based on travel direction</td><td>0%</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>8. Sensor Noise Models (Enhanced)</h2>
<p>Realistic measurement noise simulation with a multi-stage pipeline.
Globally disabled by default. Enable on Process Models tab.</p>

<h3>Noise Pipeline</h3>
<p>Each sensor tag processes noise through six stages:</p>
<div class="eq">
<b>1. Raw Noise:</b> n(t) = N(0, &sigma;) &nbsp; [Gaussian] &nbsp; or &nbsp; U(-A, +A) &nbsp; [Uniform]<br/>
<b>2. First-Order Filter:</b> y(t) = &alpha; &middot; n(t) + (1 - &alpha;) &middot; y(t-1), &nbsp; &alpha; = &Delta;t / (&tau; + &Delta;t)<br/>
<b>3. Sensor Drift:</b> d(t) = d(t-1) + N(0, r &middot; &radic;&Delta;t) &nbsp; [random walk]<br/>
<b>4. Bias Offset:</b> output = value + bias + filtered + drift<br/>
<b>5. Quantization:</b> output = round(output / step) &times; step, &nbsp; step = span / 2<sup>bits</sup><br/>
<b>6. Dead Band:</b> if |output - prev| &lt; DB then output = prev
</div>

<h3>Default Parameters</h3>
<table>
<tr><th>Tag</th><th>Type</th><th>&sigma;</th><th>&tau; (s)</th><th>Drift Rate</th><th>Category</th></tr>
<tr><td>COT, T_fluid_1&ndash;3</td><td>Gaussian</td><td>0.5 &deg;F</td><td>2.0</td><td>0.01</td><td>Temperature</td></tr>
<tr><td>T_gas</td><td>Gaussian</td><td>2.0 &deg;F</td><td>5.0</td><td>0.05</td><td>Temperature</td></tr>
<tr><td>T_flue_out, T_conv_out</td><td>Gaussian</td><td>1.0 &deg;F</td><td>3.0</td><td>0.02</td><td>Temperature</td></tr>
<tr><td>P_draft</td><td>Gaussian</td><td>2.5 Pa</td><td>0.5</td><td>0.05</td><td>Pressure</td></tr>
<tr><td>feed/fuel/air_rate</td><td>Gaussian (frac)</td><td>0.5%</td><td>1.0</td><td>0.001</td><td>Flow</td></tr>
<tr><td>O2_pct</td><td>Gaussian</td><td>0.1 vol%</td><td>8.0</td><td>0.005</td><td>Analyzer</td></tr>
<tr><td>CO_ppm</td><td>Gaussian</td><td>5.0 ppm</td><td>10.0</td><td>0.1</td><td>Analyzer</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>9. Control System (12 PID Loops)</h2>

<h3>Controller Summary</h3>
<table>
<tr><th>Tag</th><th>Service</th><th>Type</th><th>PV</th><th>Manipulates</th></tr>
<tr><td>FIC-101</td><td>Feed Flow</td><td>Standalone</td><td>feed_rate</td><td>Feed valve</td></tr>
<tr><td>TIC-101</td><td>COT (Master)</td><td>Cascade master</td><td>COT</td><td>FIC-102 SP</td></tr>
<tr><td>FIC-102</td><td>Fuel Flow (Slave)</td><td>Cascade slave</td><td>fuel_rate</td><td>Fuel valve</td></tr>
<tr><td>FIC-103</td><td>Air Flow</td><td>Cross-limited</td><td>air_rate</td><td>Air valve</td></tr>
<tr><td>PIC-101</td><td>Draft Pressure</td><td>Standalone (direct)</td><td>P_draft</td><td>Damper</td></tr>
<tr><td>AIC-101</td><td>O2 Trim</td><td>Slow trim</td><td>O2_pct</td><td>Air ratio bias</td></tr>
<tr><td>AIC-102</td><td>CO Safety</td><td>Override (direct)</td><td>CO_ppm</td><td>CO correction</td></tr>
<tr><td>FIC-101A..D</td><td>Pass 1&ndash;4 Flow</td><td>Standalone</td><td>pass flow</td><td>Pass valve</td></tr>
<tr><td>PIC-102</td><td>Fuel Gas Pressure</td><td>Override (direct)</td><td>P_fuel_gas</td><td>MIN &rarr; Fuel valve</td></tr>
</table>

<h3>Tuning Parameters</h3>
<table>
<tr><th>Tag</th><th>Kp</th><th>Ti (s)</th><th>Td</th><th>Beta</th><th>Alpha</th><th>Rate Lim</th><th>Advanced</th></tr>
<tr><td>FIC-101</td><td>0.005</td><td>15</td><td>0</td><td>1.0</td><td>0.1</td><td>&mdash;</td><td>&mdash;</td></tr>
<tr><td>TIC-101</td><td>0.02</td><td>120</td><td>0</td><td>0.7</td><td>0.1</td><td>&mdash;</td><td>DB=0.3K, DRLA, SP ramp, output filter</td></tr>
<tr><td>FIC-102</td><td>0.8</td><td>8</td><td>0</td><td>1.0</td><td>0.1</td><td>5%/scan</td><td>&mdash;</td></tr>
<tr><td>FIC-103</td><td>0.015</td><td>15</td><td>0</td><td>1.0</td><td>0.1</td><td>5%/scan</td><td>&mdash;</td></tr>
<tr><td>PIC-101</td><td>0.005</td><td>120</td><td>0</td><td>0.5</td><td>0.1</td><td>&mdash;</td><td>&mdash;</td></tr>
<tr><td>AIC-101</td><td>0.05</td><td>300</td><td>0</td><td>1.0</td><td>0.1</td><td>&mdash;</td><td>DB=0.1%, gap action</td></tr>
<tr><td>AIC-102</td><td>0.001</td><td>60</td><td>0</td><td>1.0</td><td>0.1</td><td>&mdash;</td><td>&mdash;</td></tr>
<tr><td>FIC-101A..D</td><td>0.015</td><td>20</td><td>0</td><td>1.0</td><td>0.1</td><td>&mdash;</td><td>&mdash;</td></tr>
<tr><td>PIC-102</td><td>2e-5</td><td>9999</td><td>0</td><td>1.0</td><td>0.1</td><td>&mdash;</td><td>P-only (override)</td></tr>
</table>

<h3>Cross-Limiting (Fuel-Air Balance)</h3>
<div class="eq">
<b>On fuel increase:</b> fuel_for_air = max(actual_fuel, demand_fuel) &mdash; air leads<br/>
<b>On fuel decrease:</b> fuel_for_air = min(actual_fuel, demand_fuel) &mdash; fuel leads<br/><br/>
air_SP = fuel_for_air &times; STOICH_AIR_RATIO &times; (1 + excess_air) &times; (1 + O2_trim)
</div>

<h3>Fuel Valve MIN Selector (PIC-102 Override)</h3>
<div class="eq">
fuel_valve = MIN(FIC-102/OUT, PIC-102/OUT)
</div>
<p>PIC-102 monitors fuel gas burner header pressure. Normally output &asymp; 1.0
(non-limiting). If header pressure drops below SP (140 kPa), PIC-102 output falls,
overriding FIC-102 via the MIN selector to reduce fuel consumption.</p>

<h3>ISA-18.2 Alarm Management</h3>
<div class="new"><b>NEW:</b> Full ISA-18.2 alarm state machine.</div>
<table>
<tr><th>State</th><th>Description</th></tr>
<tr><td>NORMAL</td><td>No alarm condition</td></tr>
<tr><td>UNACKNOWLEDGED</td><td>Alarm active, not yet acknowledged by operator</td></tr>
<tr><td>ACKNOWLEDGED</td><td>Alarm active and acknowledged</td></tr>
<tr><td>SHELVED</td><td>Temporarily suppressed with auto-return timer (default 8 hr)</td></tr>
<tr><td>SUPPRESSED</td><td>Suppressed by logic (e.g., unit not in service)</td></tr>
<tr><td>OUT_OF_SERVICE</td><td>Disabled by maintenance</td></tr>
</table>

<h4>4 Priority Levels</h4>
<table>
<tr><th>Priority</th><th>Color</th><th>Description</th></tr>
<tr><td>CRITICAL</td><td style="color: #FF0000;">Red</td><td>Immediate operator action required (e.g., CO trip)</td></tr>
<tr><td>HIGH</td><td style="color: #FF8C00;">Orange</td><td>Prompt action required (e.g., high COT)</td></tr>
<tr><td>MEDIUM</td><td style="color: #DAA520;">Yellow</td><td>Awareness needed (e.g., O2 deviation)</td></tr>
<tr><td>LOW</td><td style="color: #4169E1;">Blue</td><td>Informational (e.g., valve saturation)</td></tr>
</table>
<p>Features: first-out annunciation for trip conditions, rate-of-change (ROC) alarms,
standing alarm count per priority, alarm journal with timestamps.</p>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>10. Control Schemes</h2>

<div class="new"><b>NEW:</b> Five switchable control schemes based on industry-standard
fired heater combustion control practices. Select via the Controllers tab dropdown.</div>

<h3>Scheme Summary</h3>
<table>
<tr>
  <th>Scheme</th><th>Name</th><th>Master</th><th>Secondary</th>
  <th>Air Control</th><th>Cross-Limit</th><th>O2 Trim</th>
</tr>
<tr>
  <td><b>A</b></td><td>Direct Temperature</td><td>TIC-101</td>
  <td>&mdash; (direct to valve)</td><td>Simple ratio</td>
  <td>No</td><td>No</td>
</tr>
<tr>
  <td><b>B</b></td><td>Simple Cascade</td><td>TIC-101</td>
  <td>FIC-102 (fuel flow)</td><td>Simple ratio</td>
  <td>No</td><td>No</td>
</tr>
<tr style="background:#E8F5E9;">
  <td><b>C</b></td><td>Cascade + Cross-Limiting</td><td>TIC-101</td>
  <td>FIC-102 (fuel flow)</td><td>Cross-limited lead-lag</td>
  <td><b>Yes</b></td><td><b>Yes</b></td>
</tr>
<tr>
  <td><b>D</b></td><td>Fired Duty Control</td><td>TIC-101</td>
  <td>FIC-102 (fired duty)</td><td>Cross-limited lead-lag</td>
  <td><b>Yes</b></td><td><b>Yes</b></td>
</tr>
<tr>
  <td><b>E</b></td><td>Fuel Pressure Control</td><td>TIC-101</td>
  <td>PIC-102 (fuel pressure)</td><td>Simple ratio</td>
  <td>No</td><td>No</td>
</tr>
</table>

<h3>Scheme A &mdash; Direct Temperature Control</h3>
<div class="eq">
<b>Block diagram:</b><br/>
COT &rarr; [TIC-101] &rarr; Fuel Valve<br/>
Fuel flow &rarr; &times; Stoich Ratio &rarr; Air SP &rarr; [FIC-103] &rarr; Air Valve
</div>
<p><b>Description:</b> The simplest control strategy. TIC-101 output [0&ndash;1] directly positions
the fuel control valve. No intermediate flow controller exists.</p>
<p><b>Advantages:</b> Easy to understand, minimal configuration, no cascade dynamics.</p>
<p><b>Disadvantages:</b> Very slow response. The dead time from burners to COT thermowell
(typically several minutes) means disturbances in fuel pressure or heating value cause
large temperature excursions before the controller responds.</p>
<p><b>Per King:</b> <i>&ldquo;Direct control requires several minutes to respond to fuel pressure changes.&rdquo;</i></p>
<p><b>When to use:</b> Training/education. Understanding why cascade is needed.</p>

<h3>Scheme B &mdash; Simple Cascade (TIC-101 &rarr; FIC-102)</h3>
<div class="eq">
<b>Block diagram:</b><br/>
COT &rarr; [TIC-101] &rarr; Fuel SP &rarr; [FIC-102] &rarr; Fuel Valve<br/>
Fuel flow &rarr; &times; Stoich Ratio &rarr; Air SP &rarr; [FIC-103] &rarr; Air Valve
</div>
<p><b>Description:</b> TIC-101 (temperature master) cascades its output as the setpoint to
FIC-102 (fuel flow slave). The inner flow loop rejects fuel supply pressure disturbances
much faster than the temperature loop can.</p>
<p><b>Advantages:</b> Faster disturbance rejection than Scheme A. The fuel flow controller
corrects for fuel supply pressure variations within seconds, before they affect COT.</p>
<p><b>Disadvantages:</b> No cross-limiting &mdash; on a rapid fuel increase, air may lag behind,
briefly creating sub-stoichiometric combustion. No O2 optimization.</p>
<p><b>Per King:</b> <i>&ldquo;By detecting and correcting disturbances more quickly than direct control,
cascade control enhances temperature regulation and reduces deviations.&rdquo;</i></p>
<p><b>When to use:</b> When fuel gas composition is stable and there is no concern about
transient air shortages during load changes.</p>

<h3>Scheme C &mdash; Cascade + Cross-Limiting (Default, Industry Standard)</h3>
<div class="eq">
<b>Block diagram:</b><br/>
COT &rarr; [TIC-101] &rarr; Fuel SP &rarr; [FIC-102] &rarr; MIN(FIC102, PIC102) &rarr; Fuel Valve<br/>
<br/>
<b>Cross-limiting (lead-lag):</b><br/>
On fuel increase: fuel_for_air = MAX(actual_fuel, demand_fuel) &mdash; air leads<br/>
On fuel decrease: fuel_for_air = MIN(actual_fuel, demand_fuel) &mdash; fuel leads<br/>
<br/>
air_SP = fuel_for_air &times; stoich_ratio &times; (1 + excess) &times; (1 + O2_trim)<br/>
O2_trim &larr; [AIC-101] &larr; Stack O2%<br/>
<br/>
<b>Burner pressure override:</b><br/>
PIC-102 monitors P_fuel_gas. If P &lt; SP: output falls, overrides FIC-102 via MIN selector.
</div>
<p><b>Description:</b> The industry-standard scheme. Full cascade with cross-limiting ensures
safe combustion at all times. On increasing demand, air flow is increased <i>before</i> fuel;
on decreasing demand, fuel is reduced <i>before</i> air. This prevents transient sub-stoichiometric
combustion.</p>
<p><b>Additional features:</b></p>
<ul>
<li>AIC-101 (O2 trim) slowly adjusts the air/fuel ratio to target optimal stack O2 (typically 1&ndash;4%)</li>
<li>PIC-102 burner pressure override prevents flame-out from low fuel pressure</li>
<li>AIC-102 CO safety override increases air if CO rises</li>
</ul>
<p><b>Per King:</b> <i>&ldquo;The air leads the fuel on increasing demand but lags it on decreasing demand.
This prevents dangerous sub-stoichiometric combustion.&rdquo;</i></p>
<p><b>When to use:</b> Standard operating scheme for most fired heaters and boilers.</p>

<h3>Scheme D &mdash; Fired Duty Control (SG-Compensated)</h3>
<div class="eq">
<b>Block diagram:</b><br/>
COT &rarr; [TIC-101] &rarr; Duty SP [MW] &rarr; [FIC-102 (duty)] &rarr; Fuel Valve<br/>
<br/>
<b>Fuel measurement:</b><br/>
F<sub>energy</sub> = F<sub>measured</sub> &times; &radic;(SG<sub>cal</sub>/SG) &times; &radic;(P/P<sub>cal</sub>) &times; &radic;(T<sub>cal</sub>/T) &times; (a&times;SG + b)<br/>
<br/>
<b>Where:</b> SG from densitometer infers NHV via: NHV &asymp; a &times; SG + b
</div>
<p><b>Description:</b> Same cascade + cross-limiting structure as Scheme C, but the fuel flow
measurement is converted to <b>energy units</b> (MJ/hr or MW) using fuel gas specific gravity (SG)
from a densitometer. This automatically compensates for fuel gas composition changes.</p>
<p><b>Key insight:</b> When fuel gas molecular weight increases, a standard flow meter sees less flow,
and the controller opens the valve. But heating value also increases with MW, so we actually need
<i>less</i> fuel. The SG correction resolves this contradiction by measuring fired duty rather than
raw fuel flow.</p>
<p><b>Advantages:</b></p>
<ul>
<li>Automatic compensation for fuel gas composition changes</li>
<li>Constant process gain between COT and fuel measurement regardless of fuel properties</li>
<li>Better tuning stability across varying fuel compositions</li>
</ul>
<p><b>Disadvantages:</b> Requires a reliable densitometer (probe-type recommended for fast response).
The densitometer infers NHV from SG; this correlation breaks down if hydrogen content varies
significantly (&gt;~10 vol%).</p>
<p><b>Per King:</b> <i>&ldquo;Multiplying the corrected flow by the inferred NHV to obtain duty measurement.&rdquo;</i></p>
<p><b>When to use:</b> Sites with variable fuel gas composition (e.g., multiple fuel sources,
refinery fuel gas headers).</p>

<h3>Scheme E &mdash; Fuel Pressure Control (Educational)</h3>
<div class="eq">
<b>Block diagram:</b><br/>
COT &rarr; [TIC-101] &rarr; Pressure SP &rarr; [PIC-102] &rarr; Fuel Valve<br/>
Fuel flow &rarr; &times; Stoich Ratio &rarr; Air SP &rarr; [FIC-103] &rarr; Air Valve
</div>
<p><b>Description:</b> TIC-101 cascades to PIC-102 (fuel gas header pressure) rather than FIC-102
(fuel flow). This is sometimes installed in the belief that it gives improved control and
inherently limits burner pressure.</p>
<p><b>Why it&rsquo;s problematic:</b></p>
<ul>
<li><b>Process gain varies:</b> Flow depends on both pressure and number of burners.
Changing from 5 to 4 burners increases the gain by 25%. This makes tuning difficult.</li>
<li><b>Wrong initial direction:</b> Shutting a burner increases pressure to remaining burners;
the pressure controller closes the valve — the <i>opposite</i> of what&rsquo;s needed.
The feedback controller must then recover from this initial wrong move.</li>
<li><b>Prevents advanced schemes:</b> Cannot implement feedforward on inlet temperature or
SG compensation, because these require a flow measurement as the secondary.</li>
<li><b>Composition correction is poor:</b> Pressure responds to &radic;density, not inversely
to heating value as required.</li>
</ul>
<p><b>Per King:</b> <i>&ldquo;Pressure control prevents implementation of many advanced regulatory
controls including feedforward and combustion air optimization.&rdquo; ... &ldquo;The overall
performance is likely to be improved by switching to directly manipulate the valve
rather than cascade it to a pressure controller.&rdquo;</i></p>
<p><b>When to use:</b> Educational purposes only. Demonstrates why flow control is preferred
over pressure control as the secondary loop.</p>

<h3>Comparison: Disturbance Response</h3>
<table>
<tr><th>Disturbance</th><th>A (Direct)</th><th>B (Cascade)</th><th>C (Cross-Limit)</th><th>D (Duty)</th><th>E (Pressure)</th></tr>
<tr><td>Fuel supply pressure drop</td>
  <td>Slow (minutes)</td><td>Fast (seconds)</td><td>Fast + safe air</td>
  <td>Fast + safe air</td><td>Medium (wrong initial direction)</td></tr>
<tr><td>Fuel gas composition change</td>
  <td>Slow, wrong gain</td><td>Slow, wrong gain</td><td>Slow, wrong gain</td>
  <td><b>Auto-compensated</b></td><td>Partial (√ρ only)</td></tr>
<tr><td>Rapid load increase</td>
  <td>Slow, no air lead</td><td>Fast, no air lead</td>
  <td><b>Fast, air leads fuel</b></td><td><b>Fast, air leads fuel</b></td><td>Medium, no air lead</td></tr>
<tr><td>Burner taken out of service</td>
  <td>OK</td><td>OK</td><td>OK</td><td>OK</td>
  <td><b>Wrong initial direction</b></td></tr>
</table>

<h3>Switching Between Schemes</h3>
<p>Use the <b>Control Scheme</b> dropdown on the Controllers tab. The simulator performs
bumpless transfer when switching: affected controllers are momentarily placed in
initialization mode to prevent output bumps. The active scheme is preserved across
simulation resets.</p>
<div class="note">
<b>Educational exercise:</b> Apply a fuel gas composition disturbance (Alarms &amp; Sim tab)
and compare the COT response across schemes C and D. Scheme D (fired duty) will show
significantly less temperature deviation because the SG correction compensates for the
composition change before the temperature controller needs to respond.
</div>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>11. PID Implementation</h2>
<!-- (was Section 10 before control schemes were added) -->

<h3>2DOF ISA Position-Form PID</h3>
<div class="eq">
P = Kp &times; (&beta; &times; SP - PV)<br/>
I += Kp &times; (dt / Ti) &times; (SP - PV) &nbsp;&nbsp; (accumulated each scan)<br/>
D(n) = c<sub>1</sub> &times; D(n-1) + c<sub>2</sub> &times; (&gamma; &times; &Delta;SP - &Delta;PV)<br/><br/>
where c<sub>1</sub> = &alpha;&times;Td / (&alpha;&times;dt + Td), &nbsp; c<sub>2</sub> = Kp&times;Td / (&alpha;&times;dt + Td)<br/><br/>
OUT = P + I + D &nbsp;&nbsp; (clamped to [out_min, out_max])
</div>

<h3>Advanced PID Features</h3>
<div class="new"><b>NEW:</b> Advanced PID capabilities.</div>
<table>
<tr><th>Feature</th><th>Parameter</th><th>Description</th></tr>
<tr><td>Error dead band</td><td><code>dead_band</code></td><td>If |error| &lt; dead_band, error = 0. Reduces chatter on noisy measurements</td></tr>
<tr><td>Dynamic Reset Limiting (DRLA)</td><td><code>drla_enabled</code>, <code>drla_limit</code></td><td>Limits integral accumulation rate |dI/dt| &le; limit. Prevents overshoot on large SP changes</td></tr>
<tr><td>Setpoint ramping</td><td><code>sp_rate_limit</code></td><td>Gradual SP movement at configurable rate (units/s). Standard for temperature loops</td></tr>
<tr><td>Gap action</td><td><code>gap_action</code>, <code>gap_deadband</code></td><td>Reduces proportional gain when PV near SP. Gap/non-linear PID</td></tr>
<tr><td>Characterizer</td><td><code>set_pv_characterizer(fn)</code></td><td>Applies function to PV before error calculation (e.g., sqrt for &Delta;P flow)</td></tr>
<tr><td>Output smoothing</td><td><code>output_filter_alpha</code></td><td>First-order filter on output: 1.0 = no filter, 0.5 = moderate, 0.1 = heavy</td></tr>
<tr><td>External reset feedback</td><td><code>bkcal_in</code></td><td>When slave saturates, master integral tracks actual slave output (proper ISA BKCAL)</td></tr>
</table>

<h3>Anti-Reset Windup (Back-Calculation)</h3>
<div class="eq">
When OUT saturates at out_min or out_max:<br/>
I = OUT<sub>clamped</sub> - P - D &nbsp;&nbsp; (back-calculate integral immediately)
</div>

<h3>BKCAL (Cascade Anti-Windup)</h3>
<div class="eq">
If |OUT - bkcal_in| &gt; 0.001:<br/>
&nbsp;&nbsp; undo this scan's integral update &nbsp; (I -= Kp &times; dt/Ti &times; error)
</div>

<h3>Controller Modes</h3>
<table>
<tr><th>Mode</th><th>Description</th></tr>
<tr><td>AUTO</td><td>Normal PID computation &mdash; output from PV/SP error</td></tr>
<tr><td>MANUAL</td><td>Operator sets output directly. Integral tracks for bumpless transfer</td></tr>
<tr><td>CASCADE</td><td>SP received from upstream master via sp_external</td></tr>
<tr><td>RCAS</td><td>Remote CAS &mdash; SP from external source (MPC/APC, ratio/override)</td></tr>
<tr><td>ROUT</td><td>Remote OUT &mdash; output driven externally (used by override logic)</td></tr>
<tr><td>IMAN</td><td>Initialization Manual &mdash; integral tracks bkcal_in for cascade startup</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>12. Step Test &amp; FOPDT Identification</h2>

<h3>FOPDT Model</h3>
<div class="eq">
y(t) = K &times; (1 - exp(-(t - &theta;) / &tau;)) &nbsp;&nbsp; [first-order plus dead time]
</div>

<h3>Identification Methods</h3>
<table>
<tr><th>Parameter</th><th>Primary Method</th><th>Alternative</th></tr>
<tr><td>Gain K</td><td>&Delta;y<sub>final</sub> / &Delta;MV<sub>step</sub></td><td>&mdash;</td></tr>
<tr><td>Dead time &theta;</td><td>5% threshold crossing</td><td>Maximum slope method</td></tr>
<tr><td>Time constant &tau;</td><td>63.2% rise time</td><td>&mdash;</td></tr>
</table>

<h3>Maximum Slope Dead Time (Alternative)</h3>
<div class="new"><b>NEW:</b> More robust for integrating/inverse processes.</div>
<div class="eq">
&theta;<sub>max_slope</sub> = t<sub>inflection</sub> - &Delta;y<sub>final</sub> / max(dy/dt)
</div>
<p>Finds point of maximum slope on the response curve. More robust than threshold crossing for processes with initial inverse response.</p>

<h3>Statistical Validation</h3>
<div class="new"><b>NEW:</b> Model quality metrics for FOPDT identification.</div>
<table>
<tr><th>Metric</th><th>Formula</th><th>Description</th></tr>
<tr><td>R&sup2;</td><td><code>1 - SS_res / SS_tot</code></td><td>Coefficient of determination: how well FOPDT fits data</td></tr>
<tr><td>95% Confidence Intervals</td><td>Bootstrap (N=200 resamples)</td><td>Uncertainty bounds on K, &tau;, &theta;</td></tr>
<tr><td>Residual autocorrelation</td><td>Durbin-Watson test</td><td>Detects systematic model error</td></tr>
<tr><td>Residual bias</td><td>Mean residual &ne; 0</td><td>Detects systematic over/under-prediction</td></tr>
</table>

<h3>MIMO Interaction Analysis</h3>
<div class="new"><b>NEW:</b> Multi-input multi-output interaction quantification.</div>
<table>
<tr><th>Analysis</th><th>Method</th><th>Description</th></tr>
<tr><td>Bristol RGA</td><td>&Lambda; = K .* inv(K)<sup>T</sup></td><td>Relative gain array from steady-state gain matrix</td></tr>
<tr><td>Dynamic RGA</td><td>G(j&omega;) at specified frequency</td><td>Frequency-dependent interaction analysis</td></tr>
<tr><td>Bode plot</td><td>FFT of step test data</td><td>Magnitude and phase vs. frequency</td></tr>
<tr><td>Nyquist</td><td>G(j&omega;) = K&times;e<sup>-j&omega;&theta;</sup> / (1+j&omega;&tau;)</td><td>Analytical from FOPDT parameters</td></tr>
</table>
<p>RGA matrix displayed as color-coded heatmap on Step Test tab. Diagonal &gt; 1.0 indicates strong interaction.</p>

<h3>Step Test Modes</h3>
<table>
<tr><th>Mode</th><th>Description</th></tr>
<tr><td>DOUBLET</td><td>Positive step then negative step; responses averaged</td></tr>
<tr><td>POSITIVE</td><td>Positive step only</td></tr>
<tr><td>NEGATIVE</td><td>Negative step only</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>13. Dynamic Disturbance Models</h2>

<h3>Five Disturbance Channels</h3>
<p>ambient_temp, feed_temp, feed_flow, fuel_press, fuel_comp</p>

<table>
<tr><th>Type</th><th>Model</th></tr>
<tr><td>Gaussian</td><td>value = N(0, &sigma;)</td></tr>
<tr><td>Random Walk</td><td>dx = -revert &times; x &times; dt + &sigma;<sub>walk</sub> &times; &radic;dt &times; N(0,1), clamped to &plusmn;limit<br/>(Ornstein-Uhlenbeck process; default revert=0.01/s, limit=5.0)</td></tr>
<tr><td>Sinusoidal</td><td>value = A &times; sin(2&pi;t / period + &phi;) &nbsp; (default period: 86400 s = 24 h)</td></tr>
<tr><td>Step</td><td>value = 0 for t &lt; t<sub>step</sub>, then magnitude</td></tr>
<tr><td>Ramp</td><td>value linearly increases from 0 to magnitude over [t<sub>start</sub>, t<sub>end</sub>]</td></tr>
</table>

<h4>Fuel Composition Interpolation</h4>
<p>Disturbance offset &isin; [-1, +1] interpolates between three compositions:</p>
<table>
<tr><th>Composition</th><th>CH<sub>4</sub></th><th>C<sub>2</sub>H<sub>6</sub></th><th>C<sub>3</sub>H<sub>8</sub></th><th>H<sub>2</sub></th><th>N<sub>2</sub></th></tr>
<tr><td>Heavy (offset -1)</td><td>0.50</td><td>0.25</td><td>0.15</td><td>0.03</td><td>0.07</td></tr>
<tr><td>Design (offset 0)</td><td>0.70</td><td>0.15</td><td>0.05</td><td>0.08</td><td>0.02</td></tr>
<tr><td>H2-rich (offset +1)</td><td>0.45</td><td>0.10</td><td>0.03</td><td>0.40</td><td>0.02</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>14. Unit Conversions (Internal SI &rarr; Display FPS)</h2>

<table>
<tr><th>Quantity</th><th>SI (internal)</th><th>Display</th><th>Conversion</th></tr>
<tr><td>Temperature</td><td>K</td><td>&deg;F</td><td>&deg;F = K &times; 9/5 - 459.67</td></tr>
<tr><td>Feed Flow</td><td>kg/s</td><td>BPD</td><td>BPD = kg/s &times; 685.7 (API 32 crude)</td></tr>
<tr><td>Gas Flow</td><td>kg/s</td><td>MMSCFD</td><td>MMSCFD = kg/s &times; 2.876 (fuel) / 1.847 (air)</td></tr>
<tr><td>Pressure</td><td>Pa</td><td>inH2O</td><td>inH2O = Pa / 249.089</td></tr>
<tr><td>Duty</td><td>W</td><td>MMBtu/h</td><td>MMBtu/h = W &times; 3.412e-6</td></tr>
<tr><td>Fraction</td><td>0&ndash;1</td><td>%</td><td>% = fraction &times; 100</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>15. Dashboard Guide</h2>

<h3>Dashboard Tabs</h3>
<table>
<tr><th>Tab</th><th>Content</th></tr>
<tr><td>Operator</td><td>DCS-style operator screen: QPainter process graphic with live values, controller faceplates, alarm banner, KPI bar, quick actions</td></tr>
<tr><td>Overview</td><td>PFD with live overlays (zone temps, NOx, vapor fraction, tube &Delta;P), 13 KPI cards, valve bars, per-pass table</td></tr>
<tr><td>Temperatures</td><td>6 trend panes: pass fluids, tube metals, zone temps &amp; refractory, zoned heat duties, feed flows, vapor fraction per pass</td></tr>
<tr><td>Controllers</td><td>Control panel + 12 ISA-101 faceplates with tuning dialog (includes advanced PID params)</td></tr>
<tr><td>Combustion</td><td>Fuel gas KPIs (SG, NHV, Wobbe), composition bar chart, combustion metrics (8 KPIs: Air/Fuel, O2, CO, NOx, SO2, Flame Temp, Residence), fired duty &amp; emissions trends</td></tr>
<tr><td>Alarms &amp; Sim</td><td>Sim speed, disturbance injection, ISA-18.2 alarm list with priority filtering &amp; shelving</td></tr>
<tr><td>Process Models</td><td>Sensor noise config, valve nonlinearity models (Karnopp, positioner, Cv), dynamic disturbance channels</td></tr>
<tr><td>Step Test</td><td>MV/CV step test config, live MV trend, CV response panes, FOPDT results with R&sup2; &amp; confidence intervals, RGA matrix, Bode plot</td></tr>
<tr><td>Historian</td><td>Multi-pane trend viewer with tag browser, live/historical modes, CSV export</td></tr>
</table>

<h3>Controller Faceplates</h3>
<ul>
<li>Left border color indicates mode: <span style="color:#2E8B2E">green = AUTO</span>,
    <span style="color:#DAA520">yellow = MANUAL</span>,
    <span style="color:#4169E1">blue = CASCADE</span></li>
<li>Displays PV, SP, and output bar in compact ISA-101 layout</li>
<li>ARW badges (HI/LO) appear when output is limited</li>
<li>Click faceplate for detail: SP write, mode change, tuning, advanced PID parameters (dead band, DRLA, gap action)</li>
</ul>

<h3>Keyboard Shortcuts</h3>
<table>
<tr><th>Key</th><th>Action</th></tr>
<tr><td>Alt+1..9</td><td>Switch tabs</td></tr>
<tr><td>Space</td><td>Pause / Resume simulation</td></tr>
<tr><td>Ctrl+R</td><td>Reset to initial conditions</td></tr>
<tr><td>Ctrl+T</td><td>Toggle tag browser dock</td></tr>
<tr><td>F1</td><td>Open this Process Reference</td></tr>
<tr><td>F11</td><td>Toggle fullscreen</td></tr>
<tr><td>F12</td><td>Take screenshot</td></tr>
<tr><td>Ctrl+Q</td><td>Quit application</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>16. Process Model Tab</h2>

<h3>Accessing the Process Model View</h3>
<p>Open from <b>View &rarr; Process Models</b> (Ctrl+3) to see a floating window with real-time process model data.</p>

<div class="new"><b>NEW &mdash; Dashboard (Ctrl+0):</b> The Dashboard window includes a <b>Heat Balance Sankey diagram</b>
showing fired duty flowing to radiant, convection, stack loss, casing loss, and transient storage with
a live efficiency badge. A <b>Valve Position Summary</b> displays all 8 valves as color-coded horizontal
bar gauges (green=normal, yellow=near limits, red=saturated). A <b>Pass Temperature Profile</b> bar chart
compares all 4 pass fluid and tube metal temperatures side by side.</div>

<h3>Temperature Profile</h3>
<ul>
<li>Shows temperatures across the heater: feed inlet &rarr; convection outlet &rarr; per-pass fluid temps &rarr; COT (avg of 4 passes)</li>
<li>Tube metal temperatures for each pass</li>
<li>Firebox zone 1 (burner) and zone 2 (bridgewall) gas temps</li>
<li>Stack/flue gas exit temperature</li>
<li>Refractory layer temperatures (hot face, intermediate, cold face)</li>
</ul>

<h3>Heat Transfer Summary</h3>
<ul>
<li>Radiant duty per pass and total (Lobo-Evans method)</li>
<li>Convection duty (LMTD-based)</li>
<li>Total absorbed duty vs fired duty</li>
<li>Thermal efficiency (absorbed/fired &times; 100)</li>
<li>Heat flux per pass (W/m&sup2; and BTU/hr&middot;ft&sup2;)</li>
</ul>

<h3>Combustion Data</h3>
<ul>
<li>Fuel flow rate (kg/s and MMSCFD)</li>
<li>Air flow rate</li>
<li>Excess air percentage</li>
<li>Flue gas composition: O<sub>2</sub>%, CO<sub>2</sub>%, CO (ppm), NO<sub>x</sub> (ppm), SO<sub>2</sub> (ppm)</li>
<li>Adiabatic flame temperature</li>
<li>Heat release rate</li>
</ul>

<h3>Pressure &amp; Draft</h3>
<ul>
<li>Firebox draft (Pa and inH<sub>2</sub>O)</li>
<li>Natural draft vs actual</li>
<li>Tube-side pressure drop per pass</li>
<li>Convection bank gas-side &Delta;P</li>
<li>Stack velocity</li>
</ul>

<h3>Crude Properties at Current Conditions</h3>
<ul>
<li>Density, Cp, viscosity, thermal conductivity at each pass temperature</li>
<li>Vapor fraction (two-phase regions)</li>
<li>Reynolds number and film HTC per pass</li>
</ul>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>17. Equipment Reference</h2>

<h3>Firebox</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Dimensions (L &times; W &times; H)</td><td>20 m &times; 8 m &times; 14 m</td></tr>
<tr><td>Volume</td><td>2240 m&sup3;</td></tr>
<tr><td>Model</td><td>2-zone: burner zone (lower) + bridgewall zone (upper)</td></tr>
<tr><td>Refractory</td><td>3-layer model (hot face 3 mm, intermediate 20 mm, cold face 75 mm)</td></tr>
<tr><td>Refractory Emissivity</td><td>0.75</td></tr>
</table>

<h3>Radiant Tubes</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Configuration</td><td>4 passes &times; 60 tubes = 240 total</td></tr>
<tr><td>Material</td><td>5Cr-0.5Mo, Sch 40</td></tr>
<tr><td>Dimensions</td><td>4.5&quot; OD, 3.854&quot; ID, 20 m length</td></tr>
<tr><td>Tube Pitch</td><td>2.0 &times; OD (double-sided firing)</td></tr>
<tr><td>Cold-Plane Area Factor &alpha;</td><td>0.82</td></tr>
<tr><td>Surface Emissivity</td><td>0.90 (oxidized)</td></tr>
<tr><td>Max Tube Metal Temperature</td><td>600 &deg;C (873 K)</td></tr>
</table>

<h3>Convection Section</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Configuration</td><td>24 tubes/row &times; 10 rows = 240 tubes</td></tr>
<tr><td>Tube OD</td><td>3.5&quot;</td></tr>
<tr><td>Tube Length</td><td>8 m</td></tr>
<tr><td>Fins</td><td>5 fins/inch (197 fins/m), fin height 0.5&quot; (12.7 mm)</td></tr>
<tr><td>Extended Surface Ratio</td><td>5&times;</td></tr>
<tr><td>Fouling Resistance</td><td>0.0005 m&sup2;&middot;K/W</td></tr>
</table>

<h3>Burners</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Count</td><td>16 floor-fired burners</td></tr>
<tr><td>Capacity</td><td>8 MW each (128 MW total)</td></tr>
<tr><td>Fuel Gas Design Pressure</td><td>140 kPa (~20 psig)</td></tr>
<tr><td>Supply Pressure</td><td>350 kPa (~50 psig)</td></tr>
<tr><td>Header Volume</td><td>2.0 m&sup3;</td></tr>
</table>

<h3>Stack</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Diameter</td><td>3.5 m</td></tr>
<tr><td>Height</td><td>45 m</td></tr>
<tr><td>Draft</td><td>Natural draft driven</td></tr>
<tr><td>Damper</td><td>Butterfly/louver type, quadratic characteristic</td></tr>
</table>

<h3>Control Valves</h3>
<table>
<tr><th>Valve</th><th>Type</th><th>Time Constant</th><th>Fail Position</th><th>Characteristic</th></tr>
<tr><td>Feed</td><td>Globe</td><td>3.0 s</td><td>Closed</td><td>Equal-%</td></tr>
<tr><td>Fuel</td><td>Globe</td><td>2.0 s</td><td>Closed</td><td>Equal-%</td></tr>
<tr><td>Air</td><td>Butterfly</td><td>5.0 s</td><td>Open</td><td>Equal-%</td></tr>
<tr><td>Damper</td><td>Louver</td><td>4.0 s</td><td>Open</td><td>Linear</td></tr>
<tr><td>Pass Trim</td><td>Globe</td><td>3.0 s</td><td>Hold Last</td><td>Equal-%</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>18. Programming &mdash; Control Strategy Design</h2>

<h3>Function Block Diagram (FBD) Editor</h3>
<ul>
<li>ISA-compatible FBD programming environment</li>
<li>Canvas-based editor with drag-and-drop blocks</li>
<li>Manhattan orthogonal wire routing</li>
<li>Multi-tab support for multiple modules</li>
</ul>

<h3>Available Function Blocks</h3>
<table>
<tr><th>Block</th><th>Description</th><th>Key Parameters</th></tr>
<tr><td>AI</td><td>Analog Input &mdash; reads from process</td><td>Tag, range, engineering units</td></tr>
<tr><td>AO</td><td>Analog Output &mdash; writes to valve/actuator</td><td>Tag, range, clamp limits</td></tr>
<tr><td>PID</td><td>PID Controller &mdash; ISA-compatible</td><td>Gain, Reset, Rate, Mode, Structure</td></tr>
<tr><td>ADD/SUB/MUL/DIV</td><td>Math blocks</td><td>Two inputs, one output</td></tr>
<tr><td>SWITCH</td><td>Signal selector</td><td>Select A or B based on condition</td></tr>
<tr><td>RATIO</td><td>Ratio calculator</td><td>Ratio multiplier, bias</td></tr>
<tr><td>HI/LO SELECT</td><td>Signal selectors</td><td>Multiple inputs, selects highest/lowest</td></tr>
</table>

<h3>Creating a Control Module</h3>
<p>Step-by-step workflow:</p>
<ol>
<li>Open Strategy Designer tab (Alt+2)</li>
<li>Click &ldquo;New&rdquo; to create new module or &ldquo;Presets&rdquo; for built-in schemes</li>
<li>Drag blocks from palette to canvas</li>
<li>Configure each block (double-click to open config dialog)</li>
<li>Wire blocks: click source terminal &rarr; click destination terminal</li>
<li>Compile (validates connections and configuration)</li>
<li>Download (select modules, brings online)</li>
<li>Monitor: Enable &ldquo;Show Values&rdquo; to see live wire values</li>
</ol>

<div class="new"><b>NEW &mdash; Block Search (Ctrl+F):</b> Press Ctrl+F to search by block name, type, or tag.
Matching blocks receive a <b>gold highlight</b> and the canvas auto-centers on the first match.
A match count indicator appears in the toolbar. Press Enter to cycle through results.</div>

<h3>Typical Control Schemes</h3>
<p><b>Scheme A &mdash; Simple Loops:</b> Individual PID loops for COT, fuel, air, draft</p>
<p><b>Scheme B &mdash; Cascade:</b> COT&rarr;Fuel cascade, Air with O<sub>2</sub> trim</p>
<p><b>Scheme C &mdash; Cross-Limiting:</b> Fuel/air cross-limiting for safe transitions</p>
<p><b>Scheme D &mdash; Advanced:</b> Full cross-limiting + pass balancing + draft optimization</p>
<p><b>Scheme E &mdash; Efficiency:</b> Excess air optimization for maximum efficiency</p>

<h3>SFC &mdash; Sequential Function Charts</h3>
<div class="note"><b>Note:</b> SFC programming is planned for a future release. Currently, startup/shutdown sequences can be implemented using logic blocks and timers within the FBD editor.</div>
<p>Planned SFC features:</p>
<ul>
<li>Startup sequence: purge &rarr; pilot ignition &rarr; main burner light-off &rarr; ramp to operating</li>
<li>Shutdown sequence: controlled ramp-down &rarr; burner shutdown &rarr; purge</li>
<li>Emergency shutdown (ESD) sequences</li>
<li>Graphical step/transition editor</li>
</ul>

<h3>SIS &mdash; Safety Instrumented System</h3>
<div class="note"><b>Note:</b> The BMS (Burner Management System) preset implements basic safety interlocks. Full SIS/SIL-rated logic is planned for future release.</div>
<p>Current BMS features:</p>
<ul>
<li>High firebox temperature trip</li>
<li>Loss of flame detection (simulated)</li>
<li>High CO override and trip (200/500/1000 ppm thresholds)</li>
<li>Low draft alarm/trip</li>
<li>Fuel gas pressure monitoring</li>
<li>Emergency fuel shutoff</li>
</ul>
<p>Planned SIS features:</p>
<ul>
<li>SIL 1/2/3 rated safety functions</li>
<li>Cause &amp; effect matrix editor</li>
<li>Proof test scheduling and diagnostics</li>
<li>SIF response time analysis</li>
</ul>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>19. HMI Building</h2>

<h3>P&amp;ID Editor</h3>
<ul>
<li>Enter edit mode: Ctrl+E (Admin role required)</li>
<li>Available items: equipment shapes, piping polylines, instrument bubbles, dynamos, labels, valve symbols</li>
<li>Drag to position, right-click for properties</li>
<li>ISA-101 High Performance graphics standards</li>
<li>Save layout: Ctrl+S (JSON format)</li>
<li><b>Pipe auto-routing:</b> Manhattan orthogonal routing automatically converts diagonal segments into clean right-angle waypoints</li>
<li><b>Snap feedback:</b> Green highlight circle appears on anchor points when cursor is near during pipe drawing</li>
<li><b>Group indicators:</b> Ctrl+G groups selected items; a blue semi-transparent bounding box appears around grouped items (Ctrl+Shift+G to ungroup)</li>
<li><b>Z-order shortcuts:</b> Home = bring to front, End = send to back (also available via right-click menu)</li>
</ul>

<h3>ISA-101 Theme</h3>
<ul>
<li>Dark background (#2D2D30) for reduced eye strain</li>
<li>Muted element colors per ISA-101 guidelines</li>
<li>Alarm color coding:
  <ul>
  <li><span style="color:#CC0000"><b>Red:</b></span> Critical/high priority alarm</li>
  <li><span style="color:#E69500"><b>Orange:</b></span> Warning/medium priority</li>
  <li><span style="color:#4169E1"><b>Blue:</b></span> Advisory/low priority</li>
  <li><span style="color:#2E8B2E"><b>Green:</b></span> Normal/OK state</li>
  </ul>
</li>
<li>Mode colors:
  <ul>
  <li><span style="color:#2E8B2E"><b>Green:</b></span> Auto</li>
  <li><span style="color:#DAA520"><b>Yellow:</b></span> Manual</li>
  <li><span style="color:#00CED1"><b>Cyan:</b></span> Cascade</li>
  <li><span style="color:#808080"><b>Gray:</b></span> Out of Service</li>
  <li><span style="color:#E69500"><b>Orange:</b></span> Initializing</li>
  </ul>
</li>
</ul>

<h3>Dynamo Configuration</h3>
<ul>
<li><b>CompactDynamo:</b> small footprint for P&amp;ID overlay
  <ul>
  <li>Bar graph (green/yellow/red zones)</li>
  <li>Tag name, current value</li>
  <li>Mode badge (colored indicator)</li>
  </ul>
</li>
<li><b>InlineDynamo:</b> medium footprint with more detail</li>
<li>Full faceplate on click</li>
</ul>

<h3>Live Data Overlays</h3>
<ul>
<li>Numeric value displays with engineering units</li>
<li>Color changes on alarm state</li>
<li>Valve position percentage on bowtie symbols</li>
<li>Pipe color coding by service (crude=blue, fuel=brown, air=teal, flue gas=gray dashed)</li>
</ul>

<h3>Custom Display Building</h3>
<div class="note"><b>Note:</b> The P&amp;ID editor supports placing and arranging standard ISA elements. Custom widget creation requires Python/PySide6 programming.</div>
<p>Steps to customize P&amp;ID:</p>
<ol>
<li>Switch to Admin role (User &rarr; Switch Role)</li>
<li>Enter edit mode (Ctrl+E)</li>
<li>Right-click canvas for context menu &rarr; Add items</li>
<li>Configure each item&rsquo;s properties (tag binding, display range, units)</li>
<li>Save layout</li>
</ol>

<!-- ═══════════════════════════════════════════════════════════════ -->
<h2>20. OPC UA Integration</h2>

<h3>Starting the OPC Server</h3>
<ul>
<li>Menu: OPC UA &rarr; Start Server</li>
<li>Endpoint: <code>opc.tcp://localhost:48470</code></li>
<li>Server name: &ldquo;Fired Heater Simulator OPC UA Server&rdquo;</li>
</ul>

<h3>Tag Namespace</h3>
<ul>
<li>All SharedDataStore keys are exposed as OPC UA variables</li>
<li>Process variables: temperatures, pressures, flows, valve positions</li>
<li>Controller state: PV, SP, OUT, Mode for each PID</li>
<li>Alarm state: active alarms with severity and timestamp</li>
</ul>

<h3>Connecting External Systems</h3>
<ul>
<li>Compatible with any OPC UA client (UaExpert, Prosys, etc.)</li>
<li>DCS integration: configure OPC UA client on your DCS platform</li>
<li>SCADA: connect Ignition, WinCC, FactoryTalk</li>
<li>Historian: log data to OSIsoft PI, Wonderware Historian</li>
</ul>

<h3>Security</h3>
<ul>
<li>Default: no authentication (development/training mode)</li>
<li>For production use: configure certificates and user authentication</li>
<li>Supports anonymous and username/password modes</li>
</ul>

<h3>Performance</h3>
<ul>
<li>Update rate: tied to simulation scan rate (100 ms)</li>
<li>Typical tag count: ~200 variables</li>
<li>Subscription-based updates for efficient bandwidth</li>
</ul>

<h3>Using with External Controllers</h3>
<div class="new"><b>Workflow:</b> Connect external DCS to OPC UA &rarr; Read AI tags &rarr; Execute control logic in external DCS &rarr; Write AO values back via OPC UA &rarr; Simulator responds to valve commands</div>
<p>This enables:</p>
<ul>
<li>Testing ISA control strategies against the simulated process</li>
<li>Training operators on realistic process dynamics</li>
<li>Validating alarm management configurations</li>
<li>Testing historian and reporting integrations</li>
</ul>

<h2>21. Complete ODE System (27 States)</h2>
<p>The fired heater simulation solves a system of 27 coupled ordinary differential equations.
Below is the full set, grouped by physical subsystem.</p>

<h3>Per-Pass Fluid Temperatures (States 0&ndash;3)</h3>
<p>Four radiant passes, each tracking bulk fluid temperature:</p>
<pre>
dT_fluid_i/dt = (1 / (m_fluid &middot; Cp))
    &middot; [ F_in &middot; Cp &middot; (T_in - T_fluid_i)
      + h_i &middot; A_i &middot; (T_metal_i - T_fluid_i) ]
</pre>
<p>where:</p>
<ul>
<li><b>i</b> = 1 &hellip; 4 (pass index)</li>
<li><b>T_in</b> = T_conv_out for pass 1; T_fluid_{i-1} for passes 2&ndash;4</li>
<li><b>m_fluid</b> = crude-oil mass holdup per pass (kg)</li>
<li><b>Cp</b> = crude-oil specific heat capacity (J/kg&middot;K)</li>
<li><b>F_in</b> = mass flow rate through the pass (kg/s)</li>
<li><b>h_i</b> = inside convective heat-transfer coefficient (W/m&sup2;&middot;K)</li>
<li><b>A_i</b> = inside tube surface area per pass (m&sup2;)</li>
</ul>

<h3>Per-Pass Tube Metal Temperatures (States 4&ndash;7)</h3>
<p>Four tube-metal temperatures corresponding to each radiant pass:</p>
<pre>
dT_metal_i/dt = (1 / (m_metal &middot; Cp_metal))
    &middot; [ Q_rad_zone(i) / n_passes_zone
      - h_i &middot; A_i &middot; (T_metal_i - T_fluid_i) ]
</pre>
<ul>
<li><b>m_metal</b> = tube metal mass per pass (kg)</li>
<li><b>Cp_metal</b> = tube metal specific heat (J/kg&middot;K)</li>
<li><b>Q_rad_zone(i)</b> = radiant heat absorbed by the zone containing pass i (W)</li>
<li><b>n_passes_zone</b> = number of passes in that zone</li>
</ul>

<h3>Firebox Zone 1 Gas Temperature (State 8)</h3>
<pre>
dT_z1/dt = (1 / C_z1)
    &middot; [ Q_release - Q_rad_z1 - Q_interzone
      - Q_ambient_z1 - m_flue &middot; Cp &middot; (T_z1 - T_z2_prev) ]
</pre>
<ul>
<li><b>C_z1</b> = thermal capacitance of zone-1 gas mass (J/K)</li>
<li><b>Q_release</b> = fuel combustion heat release (W)</li>
<li><b>Q_rad_z1</b> = net radiant heat transfer to tubes in zone 1 (Lobo-Evans model) (W)</li>
<li><b>Q_interzone</b> = radiation exchange between zone 1 and zone 2 (W)</li>
<li><b>Q_ambient_z1</b> = heat loss through refractory to ambient (W)</li>
<li><b>m_flue</b> = flue-gas mass flow rate (kg/s)</li>
</ul>

<h3>Flue Gas Exit Temperature (State 9)</h3>
<pre>
dT_flue/dt = (T_z2 - T_flue) / tau_flue
</pre>
<ul>
<li><b>tau_flue</b> = flue-gas transport lag time constant (s)</li>
<li><b>T_z2</b> = zone-2 gas temperature (K)</li>
</ul>

<h3>Convection Section Outlet Temperature (State 10)</h3>
<pre>
dT_conv/dt = (1 / (m_conv &middot; Cp))
    &middot; [ F_crude &middot; Cp &middot; (T_crude_in - T_conv)
      + U &middot; A_eff &middot; LMTD ]
</pre>
<ul>
<li><b>m_conv</b> = crude-oil holdup in convection tubes (kg)</li>
<li><b>F_crude</b> = total crude mass flow rate (kg/s)</li>
<li><b>T_crude_in</b> = crude inlet temperature (K)</li>
<li><b>U</b> = overall heat-transfer coefficient (W/m&sup2;&middot;K)</li>
<li><b>A_eff</b> = effective (finned) surface area (m&sup2;), per ESCOA correlations</li>
<li><b>LMTD</b> = log-mean temperature difference (K)</li>
</ul>

<h3>Draft Pressure (State 11)</h3>
<pre>
dP_draft/dt = (P_target - P_draft) / tau_draft
</pre>
<ul>
<li><b>P_target</b> = natural_draft &minus; friction_losses (stack-effect model) (Pa)</li>
<li><b>tau_draft</b> = draft response time constant (s)</li>
</ul>

<h3>Main Valves (States 12&ndash;15)</h3>
<p>Fuel gas, air damper, crude feed, and atomizing steam valves:</p>
<pre>
dValve/dt = (demand - valve) / tau_valve
</pre>
<ul>
<li><b>demand</b> = controller output or manual setpoint (0&ndash;100 %)</li>
<li><b>tau_valve</b> = actuator time constant (s), specific to each valve</li>
<li>Nonlinear installed characteristics: linear, equal-percentage, or quick-opening</li>
</ul>

<h3>Pass Valves (States 16&ndash;22)</h3>
<p>Seven individual pass-balancing valves with similar first-order lag dynamics:</p>
<pre>
dValve_pass_j/dt = (demand_j - valve_pass_j) / tau_pass
</pre>

<h3>Fuel Gas Header Pressure (State 23)</h3>
<pre>
dP_fuel/dt = (1 / V_header)
    &middot; (F_supply - F_consumed) &middot; R &middot; T_fuel / MW_fuel
</pre>
<ul>
<li><b>V_header</b> = fuel-gas header volume (m&sup3;)</li>
<li><b>F_supply</b> = fuel supply mass flow (kg/s)</li>
<li><b>F_consumed</b> = fuel consumption by burners (kg/s)</li>
<li><b>R</b> = universal gas constant (J/mol&middot;K)</li>
<li><b>T_fuel</b> = fuel gas temperature (K)</li>
<li><b>MW_fuel</b> = fuel molecular weight (kg/mol)</li>
</ul>

<h3>Zone 2 Gas Temperature (State 24)</h3>
<pre>
dT_z2/dt = (1 / C_z2)
    &middot; [ m_flue &middot; Cp &middot; (T_z1 - T_z2)
      + Q_interzone - Q_rad_z2
      - Q_ambient_z2 - Q_flue_exit ]
</pre>
<ul>
<li><b>C_z2</b> = thermal capacitance of zone-2 gas mass (J/K)</li>
<li><b>Q_rad_z2</b> = radiant heat transfer to tubes in zone 2 (W)</li>
<li><b>Q_ambient_z2</b> = zone-2 refractory heat loss (W)</li>
<li><b>Q_flue_exit</b> = enthalpy of flue gas leaving zone 2 (W)</li>
</ul>

<h3>Refractory Temperatures (States 25&ndash;26)</h3>
<p>Two-node refractory wall model (hot interior face and cold exterior face):</p>
<pre>
dT_refr_int/dt  = (T_gas_avg - T_refr_int) / tau_int
dT_refr_cold/dt = (T_refr_int - T_refr_cold) / tau_cold
</pre>
<ul>
<li><b>T_gas_avg</b> = average firebox gas temperature (K)</li>
<li><b>tau_int</b> = interior-node thermal time constant (s)</li>
<li><b>tau_cold</b> = cold-face thermal time constant (s)</li>
</ul>

<h2>22. References</h2>

<h3>Heat Transfer &amp; Fired Heaters</h3>
<ul>
<li>Lobo, W.E. and Evans, J.E. (1939). &ldquo;Heat transfer in the radiant section of petroleum heaters.&rdquo; <i>Trans. AIChE</i>, 35, 743&ndash;778.</li>
<li>Hottel, H.C. and Sarofim, A.F. (1967). <i>Radiative Transfer</i>. McGraw-Hill, New York.</li>
<li>Kern, D.Q. (1950). <i>Process Heat Transfer</i>. McGraw-Hill, New York.</li>
<li>ESCOA Engineering Manual (2010). Extended Surface Corporation of America.</li>
<li>API Standard 530 (2015). &ldquo;Calculation of Heater-Tube Thickness in Petroleum Refineries.&rdquo; 7th ed., American Petroleum Institute.</li>
<li>API Standard 560 (2016). &ldquo;Fired Heaters for General Refinery Service.&rdquo; 5th ed., American Petroleum Institute.</li>
<li>Zukauskas, A. (1972). &ldquo;Heat Transfer from Tubes in Cross Flow.&rdquo; <i>Advances in Heat Transfer</i>, 8, 93&ndash;160.</li>
</ul>

<h3>Combustion</h3>
<ul>
<li>Turns, S.R. (2012). <i>An Introduction to Combustion: Concepts and Applications</i>. 3rd ed., McGraw-Hill.</li>
<li>Zeldovich, Y.B. (1946). &ldquo;The Oxidation of Nitrogen in Combustion and Explosions.&rdquo; <i>Acta Physicochimica USSR</i>, 21, 577&ndash;628.</li>
</ul>

<h3>Process Control</h3>
<ul>
<li>King, M. (2016). <i>Process Control: A Practical Approach</i>. 2nd ed., Wiley.</li>
<li>Seborg, D.E., Edgar, T.F., Mellichamp, D.A. and Doyle III, F.J. (2016). <i>Process Dynamics and Control</i>. 4th ed., Wiley.</li>
<li>ISA-5.1 (2009). &ldquo;Instrumentation Symbols and Identification.&rdquo; ISA.</li>
<li>ISA-101 (2015). &ldquo;Human Machine Interfaces for Process Automation Systems.&rdquo; ISA.</li>
<li>ISA-18.2 (2016). &ldquo;Management of Alarm Systems for the Process Industries.&rdquo; ISA.</li>
<li>&Aring;str&ouml;m, K.J. and H&auml;gglund, T. (2006). <i>Advanced PID Control</i>. ISA.</li>
<li>Smith, C.L. (2009). <i>Practical Process Control: Tuning and Troubleshooting</i>. Wiley.</li>
</ul>

<h3>Crude Oil Properties</h3>
<ul>
<li>API Technical Data Book (2005). &ldquo;Petroleum Refining.&rdquo; American Petroleum Institute.</li>
<li>Riazi, M.R. (2005). <i>Characterization and Properties of Petroleum Fractions</i>. ASTM International.</li>
</ul>

<h3>Two-Phase Flow</h3>
<ul>
<li>Chen, J.C. (1966). &ldquo;Correlation for Boiling Heat Transfer to Saturated Fluids in Convective Flow.&rdquo; <i>I&amp;EC Process Design and Development</i>, 5(3), 322&ndash;329.</li>
</ul>
"""


# ---------------------------------------------------------------------------
# Tennessee Eastman Process Help
# ---------------------------------------------------------------------------

TE_HELP_HTML = """
<style>
body { font-family: Segoe UI, Arial, sans-serif; background: #F5F5F5; }
h1 { color: #1A1A1A; border-bottom: 2px solid #4169E1; padding-bottom: 6px; }
h2 { color: #333; margin-top: 28px; border-bottom: 1px solid #CCC;
     padding-bottom: 4px; }
h3 { color: #4169E1; margin-top: 16px; }
h4 { color: #505050; margin-top: 12px; }
table { border-collapse: collapse; margin: 8px 0; width: 100%; }
th, td { border: 1px solid #CCC; padding: 4px 8px; text-align: left; }
th { background: #E0E0E0; }
code { background: #E8E8E8; padding: 1px 4px; border-radius: 3px;
       font-family: Consolas, monospace; }
pre { background: #E8E8E8; padding: 8px 12px; border-radius: 4px;
      font-family: Consolas, monospace; font-size: 9pt; overflow-x: auto;
      border: 1px solid #CCC; }
.eq { background: #F0F0F0; border: 1px solid #DDD; padding: 6px 12px;
      border-radius: 4px; margin: 6px 0; font-family: Consolas, monospace;
      font-size: 9.5pt; }
.note { background: #FFFDE7; border-left: 3px solid #DAA520; padding: 6px 10px;
        margin: 6px 0; font-size: 9.5pt; }
.new { background: #E8F5E9; border-left: 3px solid #2E8B2E; padding: 6px 10px;
       margin: 6px 0; font-size: 9.5pt; }
</style>

<h1>Tennessee Eastman Process &mdash; Comprehensive Reference</h1>

<!-- ================================================================== -->
<h2>1. Process Overview</h2>

<p>The <b>Tennessee Eastman (TE) process</b> is a benchmark challenge problem for
plant-wide control, first published by Downs and Vogel (1993). It represents a
realistic chemical plant producing two liquid products (G and H) from four
gaseous reactants (A, C, D, E) with an inert (B) and a byproduct (F).</p>

<h3>Plant Units</h3>
<ul>
<li><b>Reactor:</b> Continuous stirred-tank reactor (CSTR), vapor-phase, exothermic reactions,
    agitated, with external cooling water loop</li>
<li><b>Condenser:</b> Partial condenser on the reactor effluent vapor; cooling water removes heat
    to condense product vapors</li>
<li><b>Vapor&ndash;Liquid Separator:</b> Separates condensed liquid from non-condensed vapor;
    vapor recycles back to reactor via compressor, liquid flows to stripper</li>
<li><b>Stripper:</b> 6-tray column; steam-heated reboiler strips light components from product
    liquid; product drawn from bottom</li>
<li><b>Recycle Compressor:</b> Returns separator overhead vapor to the reactor feed</li>
</ul>

<h3>Model Scale</h3>
<table>
<tr><th>Category</th><th>Count</th><th>Description</th></tr>
<tr><td>Chemical species</td><td>8</td><td>A, B (inert), C, D, E, F (byproduct), G (product 1), H (product 2)</td></tr>
<tr><td>State variables</td><td>50</td><td>Internal DLL state vector</td></tr>
<tr><td>Measurements (xmeas)</td><td>41</td><td>Flows, temperatures, pressures, levels, compositions</td></tr>
<tr><td>Manipulated variables (xmv)</td><td>12</td><td>Valve positions and agitator speed (0&ndash;100%)</td></tr>
<tr><td>Disturbances (idv)</td><td>28</td><td>Step, random, slow-drift, and extended disturbances (Bathelt &amp; Ricker 2015)</td></tr>
<tr><td>Reactions</td><td>4</td><td>All exothermic, irreversible, vapor-phase</td></tr>
</table>

<h3>Integration Details</h3>
<ul>
<li><b>DLL:</b> <code>te_process.dll</code> &mdash; flat C API with opaque handle, wrapping
    original Fortran code</li>
<li><b>Integrator:</b> scipy <code>solve_ivp</code> with RK45 (Runge&ndash;Kutta 4th/5th order),
    0.1&thinsp;s step</li>
<li><b>Time units:</b> The DLL works internally in <b>hours</b>; the simulator converts at the
    boundary so all displayed values are in SI seconds/minutes as appropriate</li>
</ul>

<div class="note"><b>Note:</b> The TE process is inherently open-loop unstable. Without
active control, the reactor will either quench or experience a thermal runaway within
minutes.</div>

<!-- ================================================================== -->
<h2>2. Reaction Chemistry</h2>

<p>Four irreversible, exothermic, vapor-phase reactions as described by Downs &amp; Vogel (1993):</p>

<h3>Reaction 1 &mdash; Primary product G</h3>
<div class="eq">A(g) + C(g) + D(g) &rarr; G(liq) &nbsp;&nbsp; (Product)</div>

<h3>Reaction 2 &mdash; Primary product H</h3>
<div class="eq">A(g) + C(g) + E(g) &rarr; H(liq) &nbsp;&nbsp; (Product)</div>

<h3>Reaction 3 &mdash; Byproduct F (from A+E)</h3>
<div class="eq">A(g) + E(g) &rarr; F(liq) &nbsp;&nbsp; (Byproduct)</div>

<h3>Reaction 4 &mdash; Byproduct F (from D trimerization)</h3>
<div class="eq">3D(g) &rarr; 2F(liq) &nbsp;&nbsp; (Byproduct)</div>

<h3>Kinetics</h3>
<p>All reactions follow <b>Arrhenius kinetics</b> with temperature-dependent rate constants:</p>

<div class="eq">r<sub>j</sub> = k<sub>j</sub> &middot; exp(&minus;E<sub>aj</sub> / RT) &middot; &prod; C<sub>i</sub><sup>&nu;<sub>ij</sub></sup></div>

<p>where r<sub>j</sub> is the rate of reaction j, k<sub>j</sub> is the pre-exponential factor,
E<sub>aj</sub> is the activation energy, R is the gas constant, T is absolute temperature,
and C<sub>i</sub> are species concentrations with stoichiometric exponents &nu;<sub>ij</sub>.</p>

<div class="note"><b>Key insight:</b> The activation energies differ between reactions, so
the product selectivity (G/H ratio vs. F byproduct) is strongly temperature-dependent.
Higher reactor temperatures favor byproduct F formation via reactions 3 and 4.</div>

<!-- ================================================================== -->
<h2>3. Process Unit Equations</h2>

<h3>3.1 Reactor (CSTR, Vapor-Phase)</h3>

<p>The reactor operates as a well-mixed vapor-phase CSTR with cooling water heat removal.</p>

<h4>Component Mass Balance</h4>
<div class="eq">dM<sub>i</sub>/dt = F<sub>i,in</sub> &minus; F<sub>i,out</sub>
+ &sum;<sub>j</sub>(&nu;<sub>ij</sub> &middot; r<sub>j</sub> &middot; V)</div>

<p>where M<sub>i</sub> is the molar holdup of species i, F<sub>i,in</sub> and F<sub>i,out</sub>
are inlet and outlet molar flow rates, &nu;<sub>ij</sub> is the stoichiometric coefficient
of species i in reaction j, r<sub>j</sub> is the reaction rate, and V is the reactor volume.</p>

<h4>Energy Balance</h4>
<div class="eq">dT/dt = (1 / M&middot;C<sub>p</sub>) &middot;
[ &sum;(F<sub>i,in</sub>&middot;H<sub>i,in</sub>) &minus; &sum;(F<sub>i,out</sub>&middot;H<sub>i,out</sub>)
+ &sum;(r<sub>j</sub>&middot;&Delta;H<sub>j</sub>&middot;V) &minus; Q<sub>cool</sub> ]</div>

<p>where M&middot;C<sub>p</sub> is the total heat capacity, H<sub>i</sub> are species enthalpies,
&Delta;H<sub>j</sub> is the heat of reaction (negative for exothermic), and Q<sub>cool</sub> is
the cooling water duty controlled by xmv_10.</p>

<h4>Pressure</h4>
<div class="eq">P = nRT / V</div>

<p>Ideal gas law; total pressure is the sum of partial pressures of all gaseous species in the
reactor. This drives the pressure controller (PIC107 &rarr; purge valve xmv_6).</p>

<div class="note"><b>Note:</b> The reactor operates at approximately 2705 kPa gauge and
120.4&deg;C at steady state. A runaway occurs if cooling is lost &mdash; the exothermic reactions
accelerate exponentially with temperature.</div>

<h3>3.2 Condenser (Partial)</h3>

<p>The condenser partially condenses reactor effluent vapor using cooling water (xmv_11).
Product-rich heavy components (G, H, F) condense preferentially.</p>

<h4>Energy Balance</h4>
<div class="eq">dT<sub>cond</sub>/dt = (1 / M<sub>cond</sub>&middot;C<sub>p</sub>) &middot;
[ F<sub>vap,in</sub>&middot;H<sub>vap</sub> &minus; F<sub>vap,out</sub>&middot;H<sub>vap</sub>
&minus; F<sub>liq,out</sub>&middot;H<sub>liq</sub> &minus; Q<sub>CW</sub> ]</div>

<p>Vapor-liquid equilibrium (VLE) flash calculations at condenser conditions determine the
split between vapor (recycled) and liquid (to separator).</p>

<h3>3.3 Vapor&ndash;Liquid Separator</h3>

<p>Gravity separation of two-phase condenser effluent. Liquid level is a key controlled variable.</p>

<h4>Liquid Level</h4>
<div class="eq">dV<sub>liq</sub>/dt = F<sub>in,liq</sub> &minus; F<sub>out,liq</sub></div>

<p>where V<sub>liq</sub> is the liquid volume, F<sub>in,liq</sub> is liquid from the condenser,
and F<sub>out,liq</sub> is liquid to the stripper (controlled by xmv_7 via LIC112).
Separator level (xmeas_12) must be maintained near 50%.</p>

<p>Vapor exits overhead to the recycle compressor. Separator temperature (xmeas_11, nominally
80.1&deg;C) and pressure (xmeas_13) affect separation efficiency.</p>

<h3>3.4 Stripper (6-Tray Column)</h3>

<p>Steam-heated reboiler strips dissolved light gases from the liquid product stream.
Product purity depends on stripper operating conditions.</p>

<h4>Per-Tray Material Balance</h4>
<div class="eq">dM<sub>i,n</sub>/dt = L<sub>n&minus;1</sub>&middot;x<sub>i,n&minus;1</sub>
+ V<sub>n+1</sub>&middot;y<sub>i,n+1</sub>
&minus; L<sub>n</sub>&middot;x<sub>i,n</sub> &minus; V<sub>n</sub>&middot;y<sub>i,n</sub></div>

<p>where L and V are liquid and vapor flows, x and y are liquid and vapor mole fractions,
and n is the tray number. Steam flow (xmv_9) controls reboiler duty. Stripper level
(xmeas_15) is controlled by product draw valve (xmv_8) via LIC115.</p>

<h4>Per-Tray Energy Balance</h4>
<div class="eq">dH<sub>n</sub>/dt = L<sub>n&minus;1</sub>&middot;h<sub>L,n&minus;1</sub>
+ V<sub>n+1</sub>&middot;h<sub>V,n+1</sub>
&minus; L<sub>n</sub>&middot;h<sub>L,n</sub> &minus; V<sub>n</sub>&middot;h<sub>V,n</sub>
+ Q<sub>reboiler</sub>&middot;&delta;<sub>n,1</sub></div>

<h3>3.5 Recycle Compressor</h3>

<p>Compresses separator overhead vapor and returns it to the reactor. The compressor
recycle valve (xmv_5) provides anti-surge protection.</p>

<div class="eq">W<sub>comp</sub> = (&gamma; / (&gamma;&minus;1)) &middot; P<sub>in</sub> &middot; V&#775;
&middot; [(P<sub>out</sub>/P<sub>in</sub>)<sup>(&gamma;&minus;1)/&gamma;</sup> &minus; 1]
/ &eta;<sub>isen</sub></div>

<p>where W<sub>comp</sub> is compressor work (xmeas_20), &gamma; is the heat capacity ratio,
V&#775; is volumetric flow, and &eta;<sub>isen</sub> is isentropic efficiency.</p>

<!-- ================================================================== -->
<h2>4. Measurements (xmeas_1 &ndash; xmeas_41)</h2>

<h3>4.1 Process Flow and Level Measurements</h3>
<table>
<tr><th>Tag</th><th>Description</th><th>Units</th><th>Nominal</th></tr>
<tr><td><code>xmeas_1</code></td><td>A feed rate</td><td>kscmh</td><td>0.25</td></tr>
<tr><td><code>xmeas_2</code></td><td>D feed rate</td><td>kg/hr</td><td>3664</td></tr>
<tr><td><code>xmeas_3</code></td><td>E feed rate</td><td>kg/hr</td><td>4509</td></tr>
<tr><td><code>xmeas_4</code></td><td>A + C feed rate (total)</td><td>kscmh</td><td>9.35</td></tr>
<tr><td><code>xmeas_5</code></td><td>Recycle flow rate</td><td>kscmh</td><td>26.9</td></tr>
<tr><td><code>xmeas_6</code></td><td>Reactor feed rate</td><td>kscmh</td><td>42.34</td></tr>
<tr><td><code>xmeas_7</code></td><td>Reactor pressure</td><td>kPa gauge</td><td>2705</td></tr>
<tr><td><code>xmeas_8</code></td><td>Reactor level</td><td>%</td><td>75</td></tr>
<tr><td><code>xmeas_9</code></td><td>Reactor temperature</td><td>&deg;C</td><td>120.4</td></tr>
<tr><td><code>xmeas_10</code></td><td>Purge rate</td><td>kscmh</td><td>0.337</td></tr>
<tr><td><code>xmeas_11</code></td><td>Separator temperature</td><td>&deg;C</td><td>80.1</td></tr>
<tr><td><code>xmeas_12</code></td><td>Separator level</td><td>%</td><td>50.0</td></tr>
<tr><td><code>xmeas_13</code></td><td>Separator pressure</td><td>kPa gauge</td><td>2633</td></tr>
<tr><td><code>xmeas_14</code></td><td>Separator underflow (liquid outlet)</td><td>m&sup3;/hr</td><td>25.16</td></tr>
<tr><td><code>xmeas_15</code></td><td>Stripper level</td><td>%</td><td>50.0</td></tr>
<tr><td><code>xmeas_16</code></td><td>Stripper pressure</td><td>kPa gauge</td><td>3102</td></tr>
<tr><td><code>xmeas_17</code></td><td>Stripper underflow (product rate)</td><td>m&sup3;/hr</td><td>22.95</td></tr>
<tr><td><code>xmeas_18</code></td><td>Stripper temperature</td><td>&deg;C</td><td>65.7</td></tr>
<tr><td><code>xmeas_19</code></td><td>Stripper steam flow</td><td>kg/hr</td><td>230.3</td></tr>
<tr><td><code>xmeas_20</code></td><td>Compressor work</td><td>kW</td><td>341.4</td></tr>
<tr><td><code>xmeas_21</code></td><td>Reactor CW outlet temperature</td><td>&deg;C</td><td>94.6</td></tr>
<tr><td><code>xmeas_22</code></td><td>Separator CW outlet temperature</td><td>&deg;C</td><td>77.3</td></tr>
</table>

<h3>4.2 Reactor Feed Compositions</h3>
<table>
<tr><th>Tag</th><th>Description</th><th>Units</th><th>Nominal</th></tr>
<tr><td><code>xmeas_23</code></td><td>Component A in reactor feed</td><td>mol%</td><td>32.19</td></tr>
<tr><td><code>xmeas_24</code></td><td>Component B in reactor feed</td><td>mol%</td><td>8.89</td></tr>
<tr><td><code>xmeas_25</code></td><td>Component C in reactor feed</td><td>mol%</td><td>26.38</td></tr>
<tr><td><code>xmeas_26</code></td><td>Component D in reactor feed</td><td>mol%</td><td>6.88</td></tr>
<tr><td><code>xmeas_27</code></td><td>Component E in reactor feed</td><td>mol%</td><td>18.78</td></tr>
<tr><td><code>xmeas_28</code></td><td>Component F in reactor feed</td><td>mol%</td><td>1.66</td></tr>
</table>

<h3>4.3 Purge Gas Compositions</h3>
<table>
<tr><th>Tag</th><th>Description</th><th>Units</th><th>Nominal</th></tr>
<tr><td><code>xmeas_29</code></td><td>Component A in purge gas</td><td>mol%</td><td>32.96</td></tr>
<tr><td><code>xmeas_30</code></td><td>Component B in purge gas</td><td>mol%</td><td>13.82</td></tr>
<tr><td><code>xmeas_31</code></td><td>Component C in purge gas</td><td>mol%</td><td>23.98</td></tr>
<tr><td><code>xmeas_32</code></td><td>Component D in purge gas</td><td>mol%</td><td>1.26</td></tr>
<tr><td><code>xmeas_33</code></td><td>Component E in purge gas</td><td>mol%</td><td>18.58</td></tr>
<tr><td><code>xmeas_34</code></td><td>Component F in purge gas</td><td>mol%</td><td>2.26</td></tr>
<tr><td><code>xmeas_35</code></td><td>Component G in purge gas</td><td>mol%</td><td>4.84</td></tr>
<tr><td><code>xmeas_36</code></td><td>Component H in purge gas</td><td>mol%</td><td>2.30</td></tr>
</table>

<h3>4.4 Product Compositions</h3>
<table>
<tr><th>Tag</th><th>Description</th><th>Units</th><th>Nominal</th></tr>
<tr><td><code>xmeas_37</code></td><td>Component D in product</td><td>mol%</td><td>0.02</td></tr>
<tr><td><code>xmeas_38</code></td><td>Component E in product</td><td>mol%</td><td>0.32</td></tr>
<tr><td><code>xmeas_39</code></td><td>Component F in product</td><td>mol%</td><td>8.89</td></tr>
<tr><td><code>xmeas_40</code></td><td>Component G in product</td><td>mol%</td><td>53.72</td></tr>
<tr><td><code>xmeas_41</code></td><td>Component H in product</td><td>mol%</td><td>37.05</td></tr>
</table>

<div class="note"><b>Note:</b> Measurements xmeas_23 through xmeas_36 (compositions) are sampled
with realistic analyzer dead time and noise. Flow, temperature, pressure, and level measurements
(xmeas_1 through xmeas_22) include Gaussian measurement noise but have negligible dead time.</div>

<!-- ================================================================== -->
<h2>5. Manipulated Variables (xmv_1 &ndash; xmv_12)</h2>

<table>
<tr><th>Tag</th><th>Description</th><th>Range</th><th>Nominal</th></tr>
<tr><td><code>xmv_1</code></td><td>D feed flow valve</td><td>0&ndash;100%</td><td>63.1%</td></tr>
<tr><td><code>xmv_2</code></td><td>E feed flow valve</td><td>0&ndash;100%</td><td>53.3%</td></tr>
<tr><td><code>xmv_3</code></td><td>A feed flow valve</td><td>0&ndash;100%</td><td>24.6%</td></tr>
<tr><td><code>xmv_4</code></td><td>A + C feed flow valve (total)</td><td>0&ndash;100%</td><td>61.3%</td></tr>
<tr><td><code>xmv_5</code></td><td>Compressor recycle valve</td><td>0&ndash;100%</td><td>22.2%</td></tr>
<tr><td><code>xmv_6</code></td><td>Purge valve</td><td>0&ndash;100%</td><td>40.1%</td></tr>
<tr><td><code>xmv_7</code></td><td>Separator liquid outlet valve</td><td>0&ndash;100%</td><td>38.1%</td></tr>
<tr><td><code>xmv_8</code></td><td>Stripper liquid product valve</td><td>0&ndash;100%</td><td>46.5%</td></tr>
<tr><td><code>xmv_9</code></td><td>Stripper steam valve</td><td>0&ndash;100%</td><td>47.4%</td></tr>
<tr><td><code>xmv_10</code></td><td>Reactor cooling water valve</td><td>0&ndash;100%</td><td>41.1%</td></tr>
<tr><td><code>xmv_11</code></td><td>Condenser cooling water valve</td><td>0&ndash;100%</td><td>18.1%</td></tr>
<tr><td><code>xmv_12</code></td><td>Agitator speed</td><td>0&ndash;100%</td><td>50.0%</td></tr>
</table>

<div class="note"><b>Valve dynamics:</b> All valves have first-order lag dynamics with a
time constant of approximately 4&ndash;6 seconds. Valve positions are clamped to 0&ndash;100%
by the DLL. The engine enforces these limits at each integration step.</div>

<!-- ================================================================== -->
<h2>6. Disturbances (idv_1 &ndash; idv_28)</h2>

<p>The TE process includes 28 disturbances that test control system robustness.
The original 20 disturbances are from Downs &amp; Vogel (1993); disturbances 21&ndash;28
are extended disturbances from Bathelt &amp; Ricker (2015). Disturbances are activated
by setting the corresponding <code>idv_N</code> tag to 1 (active) or 0 (inactive).</p>

<h3>Step Disturbances (idv_1 &ndash; idv_7)</h3>
<table>
<tr><th>Tag</th><th>Description</th><th>Type</th></tr>
<tr><td><code>idv_1</code></td><td>A/C feed ratio change, B composition constant</td><td>Step</td></tr>
<tr><td><code>idv_2</code></td><td>B composition change, A/C ratio constant</td><td>Step</td></tr>
<tr><td><code>idv_3</code></td><td>D feed temperature change</td><td>Step</td></tr>
<tr><td><code>idv_4</code></td><td>Reactor cooling water inlet temperature change</td><td>Step</td></tr>
<tr><td><code>idv_5</code></td><td>Condenser cooling water inlet temperature change</td><td>Step</td></tr>
<tr><td><code>idv_6</code></td><td>A feed loss (partial blockage)</td><td>Step</td></tr>
<tr><td><code>idv_7</code></td><td>C header pressure loss &mdash; loss of recycle flow</td><td>Step</td></tr>
</table>

<h3>Random Disturbances (idv_8 &ndash; idv_12)</h3>
<table>
<tr><th>Tag</th><th>Description</th><th>Type</th></tr>
<tr><td><code>idv_8</code></td><td>A, B, C feed composition variation</td><td>Random</td></tr>
<tr><td><code>idv_9</code></td><td>D feed temperature variation</td><td>Random</td></tr>
<tr><td><code>idv_10</code></td><td>C feed temperature variation</td><td>Random</td></tr>
<tr><td><code>idv_11</code></td><td>Reactor cooling water inlet temperature variation</td><td>Random</td></tr>
<tr><td><code>idv_12</code></td><td>Condenser cooling water inlet temperature variation</td><td>Random</td></tr>
</table>

<h3>Special Disturbances (idv_13 &ndash; idv_20)</h3>
<table>
<tr><th>Tag</th><th>Description</th><th>Type</th></tr>
<tr><td><code>idv_13</code></td><td>Reaction kinetics drift (slow)</td><td>Slow drift</td></tr>
<tr><td><code>idv_14</code></td><td>Reactor CW valve stiction</td><td>Stiction</td></tr>
<tr><td><code>idv_15</code></td><td>Condenser CW valve stiction</td><td>Stiction</td></tr>
<tr><td><code>idv_16</code></td><td>Heat transfer fouling &mdash; random walk</td><td>Random walk</td></tr>
<tr><td><code>idv_17</code></td><td>Heat transfer fouling &mdash; random walk</td><td>Random walk</td></tr>
<tr><td><code>idv_18</code></td><td>Heat transfer fouling &mdash; random walk</td><td>Random walk</td></tr>
<tr><td><code>idv_19</code></td><td>Valve stiction &mdash; random walk</td><td>Random walk</td></tr>
<tr><td><code>idv_20</code></td><td>Valve stiction &mdash; random walk</td><td>Random walk</td></tr>
</table>

<h3>Extended Disturbances (idv_21 &ndash; idv_28) &mdash; Bathelt &amp; Ricker 2015</h3>

<div class="new"><b>New:</b> IDVs 21&ndash;28 are extended disturbances from the revised TE model
(Bathelt, Ricker &amp; Jelali, 2015). They use random-walk mechanisms to perturb feed
temperatures, feed flow ranges, and cooling water supply pressures.</div>

<table>
<tr><th>Tag</th><th>Description</th><th>Mechanism</th></tr>
<tr><td><code>idv_21</code></td><td>Feed A temperature variation</td><td>Random walk on tst[2]</td></tr>
<tr><td><code>idv_22</code></td><td>Feed E temperature variation</td><td>Random walk on tst[1]</td></tr>
<tr><td><code>idv_23</code></td><td>Feed A flow range variation</td><td>Random walk on vrng[2]</td></tr>
<tr><td><code>idv_24</code></td><td>Feed D flow range variation</td><td>Random walk on vrng[0]</td></tr>
<tr><td><code>idv_25</code></td><td>Feed E flow range variation</td><td>Random walk on vrng[1]</td></tr>
<tr><td><code>idv_26</code></td><td>Feed A+C flow range variation</td><td>Random walk on vrng[3]</td></tr>
<tr><td><code>idv_27</code></td><td>Reactor CW supply pressure variation</td><td>Random walk on vrng[9]</td></tr>
<tr><td><code>idv_28</code></td><td>Condenser CW supply pressure variation</td><td>Random walk on vrng[10]</td></tr>
</table>

<div class="note"><b>Note:</b> IDV(27) and IDV(28) are particularly challenging &mdash; they perturb
the cooling water supply capacity, which can cause reactor temperature excursions and
process shutdown if the temperature controllers cannot compensate.</div>

<div class="new"><b>Tip:</b> Start with idv_1 or idv_2 to test base regulatory control.
Disturbances idv_4 and idv_5 directly challenge the temperature controllers (TIC109, TIC111).
Disturbance idv_6 (A feed loss) is one of the most severe &mdash; many published control
structures cannot maintain stability against it. For testing extended disturbance rejection,
try IDV(21) (feed A temperature) or the combined IDV(27)+IDV(28) (CW supply pressure).</div>

<!-- ================================================================== -->
<h2>7. Mode 1 Steady-State Operating Point</h2>

<p>The nominal (Mode 1) steady-state values represent the base operating condition.
All controllers should be initialized to these values for bumpless startup.</p>

<h3>Key Process Variables</h3>
<table>
<tr><th>Tag</th><th>Value</th><th>Description</th></tr>
<tr><td><code>xmeas_7</code></td><td>2705 kPa</td><td>Reactor pressure</td></tr>
<tr><td><code>xmeas_8</code></td><td>75%</td><td>Reactor level</td></tr>
<tr><td><code>xmeas_9</code></td><td>120.4 &deg;C</td><td>Reactor temperature</td></tr>
<tr><td><code>xmeas_11</code></td><td>80.1 &deg;C</td><td>Separator temperature</td></tr>
<tr><td><code>xmeas_12</code></td><td>50.0%</td><td>Separator level</td></tr>
<tr><td><code>xmeas_13</code></td><td>2633 kPa</td><td>Separator pressure</td></tr>
<tr><td><code>xmeas_15</code></td><td>50.0%</td><td>Stripper level</td></tr>
<tr><td><code>xmeas_18</code></td><td>65.7 &deg;C</td><td>Stripper temperature</td></tr>
<tr><td><code>xmeas_20</code></td><td>341.4 kW</td><td>Compressor work</td></tr>
</table>

<h3>Nominal Valve Positions</h3>
<table>
<tr><th>Tag</th><th>Value</th><th>Description</th></tr>
<tr><td><code>xmv_1</code></td><td>63.1%</td><td>D feed flow valve</td></tr>
<tr><td><code>xmv_2</code></td><td>53.3%</td><td>E feed flow valve</td></tr>
<tr><td><code>xmv_3</code></td><td>24.6%</td><td>A feed flow valve</td></tr>
<tr><td><code>xmv_4</code></td><td>61.3%</td><td>A + C feed flow valve</td></tr>
<tr><td><code>xmv_5</code></td><td>22.2%</td><td>Compressor recycle valve</td></tr>
<tr><td><code>xmv_6</code></td><td>40.1%</td><td>Purge valve</td></tr>
<tr><td><code>xmv_7</code></td><td>38.1%</td><td>Separator liquid outlet valve</td></tr>
<tr><td><code>xmv_8</code></td><td>46.5%</td><td>Stripper liquid product valve</td></tr>
<tr><td><code>xmv_9</code></td><td>47.4%</td><td>Stripper steam valve</td></tr>
<tr><td><code>xmv_10</code></td><td>41.1%</td><td>Reactor CW valve</td></tr>
<tr><td><code>xmv_11</code></td><td>18.1%</td><td>Condenser CW valve</td></tr>
<tr><td><code>xmv_12</code></td><td>50.0%</td><td>Agitator speed</td></tr>
</table>

<!-- ================================================================== -->
<h2>8. Control Strategies</h2>

<h3>8.1 Base Regulatory Control (Skogestad, 2000)</h3>

<p>The base regulatory layer consists of 5 SISO PID loops that stabilize the most
critical process variables. This is the minimum viable control structure for
keeping the plant running.</p>

<table>
<tr><th>Controller</th><th>PV (Tag)</th><th>MV (Tag)</th><th>K<sub>p</sub></th>
    <th>T<sub>i</sub> (s)</th><th>Action</th></tr>
<tr><td><b>TIC109</b></td><td>Reactor temp (xmeas_9)</td><td>Reactor CW (xmv_10)</td>
    <td>8.0</td><td>450</td><td>Reverse</td></tr>
<tr><td><b>TIC111</b></td><td>Sep. temp (xmeas_11)</td><td>Cond. CW (xmv_11)</td>
    <td>4.0</td><td>900</td><td>Reverse</td></tr>
<tr><td><b>LIC112</b></td><td>Sep. level (xmeas_12)</td><td>Sep. liq. (xmv_7)</td>
    <td>2.0</td><td>300</td><td>Direct</td></tr>
<tr><td><b>LIC115</b></td><td>Strip. level (xmeas_15)</td><td>Strip. prod. (xmv_8)</td>
    <td>2.0</td><td>300</td><td>Direct</td></tr>
<tr><td><b>PIC107</b></td><td>Reactor press. (xmeas_7)</td><td>Purge (xmv_6)</td>
    <td>1.0</td><td>120</td><td>Direct</td></tr>
</table>

<div class="eq">Controller action: Reverse = increasing PV &rarr; increasing MV
(cooling response). Direct = increasing PV &rarr; increasing MV (drain response).</div>

<div class="note"><b>Why these 5 loops?</b> Skogestad's self-optimizing control analysis
shows that controlling reactor temperature, separator temperature, both levels, and
reactor pressure with tight loops allows the remaining degrees of freedom to be set at
constant values while maintaining near-optimal operation.</div>

<h3>8.2 Extended Control (Ricker, 1996)</h3>

<p>Ricker's decentralized control structure extends the base 5 loops to 12 loops,
adding composition control, production rate control, and feed ratio management.
This provides better disturbance rejection and product quality control.</p>

<table>
<tr><th>Controller</th><th>PV</th><th>MV</th><th>Description</th></tr>
<tr><td>TIC109</td><td>xmeas_9</td><td>xmv_10</td><td>Reactor temperature</td></tr>
<tr><td>TIC111</td><td>xmeas_11</td><td>xmv_11</td><td>Separator temperature</td></tr>
<tr><td>LIC112</td><td>xmeas_12</td><td>xmv_7</td><td>Separator level</td></tr>
<tr><td>LIC115</td><td>xmeas_15</td><td>xmv_8</td><td>Stripper level</td></tr>
<tr><td>PIC107</td><td>xmeas_7</td><td>xmv_6</td><td>Reactor pressure</td></tr>
<tr><td>LIC108</td><td>xmeas_8</td><td>xmv_4</td><td>Reactor level</td></tr>
<tr><td>FIC101</td><td>xmeas_1</td><td>xmv_3</td><td>A feed flow</td></tr>
<tr><td>FIC102</td><td>xmeas_2</td><td>xmv_1</td><td>D feed flow</td></tr>
<tr><td>FIC103</td><td>xmeas_3</td><td>xmv_2</td><td>E feed flow</td></tr>
<tr><td>FIC104</td><td>xmeas_4</td><td>xmv_4</td><td>A + C feed flow (cascade)</td></tr>
<tr><td>TIC118</td><td>xmeas_18</td><td>xmv_9</td><td>Stripper temperature</td></tr>
<tr><td>FIC105</td><td>xmeas_5</td><td>xmv_5</td><td>Recycle valve / compressor</td></tr>
</table>

<h3>8.3 DMC / APC Layer</h3>

<p>A <b>Dynamic Matrix Control (DMC)</b> layer can be configured on top of the regulatory
PID loops. The DMC controller uses step-response models to coordinate multiple
manipulated variables and controlled variables simultaneously, providing:</p>

<ul>
<li><b>Constraint handling:</b> Respects valve position limits, rate-of-change limits, and
    process variable constraints</li>
<li><b>Feedforward:</b> Measured disturbance rejection using model-predicted compensation</li>
<li><b>Multivariable coordination:</b> Accounts for process interactions (e.g., reactor
    temperature &harr; separator temperature coupling)</li>
<li><b>Economic optimization:</b> Pushes operation toward economic optimum within constraints</li>
</ul>

<div class="note"><b>APC operator view:</b> The APC panel provides DMC faceplates showing
controller status (ON/OFF), controlled variables (CV), manipulated variables (MV),
feedforward variables (FF), and constraint status. Operators can shed individual
MV&ndash;CV pairs or take the entire controller offline.</div>

<!-- ================================================================== -->
<h2>9. PID Scaling and BKCAL</h2>

<p>The strategy system uses ISA-standard function blocks with explicit scaling and
back-calculation for bumpless transfer.</p>

<h3>Signal Path</h3>
<div class="eq">AI (eng. units) &rarr; PID (0&ndash;1 output) &rarr; SCALER (0&ndash;1 &rarr; 0&ndash;100)
&rarr; AO (0&ndash;100%) &rarr; store (xmv tag)</div>

<h3>BKCAL Chain (Reverse)</h3>
<div class="eq">AO BKCAL_OUT (0&ndash;100) &rarr; SCALER inverse (0&ndash;100 &rarr; 0&ndash;1)
&rarr; PID BKCAL_IN (0&ndash;1)</div>

<p>This ensures bumpless startup: when the strategy goes online, each PID block
initializes its output to match the current valve position via the BKCAL chain.
The bridge performs 3 initialization passes to propagate BKCAL values through
SCALER blocks.</p>

<h3>Gain Normalization</h3>
<div class="eq">gain_a = K<sub>p</sub> &times; (PV_span / OUT_span)</div>

<p>Since PID output span is always 1.0 (0&ndash;1 range), the actual gain applied is
K<sub>p</sub> &times; PV_span. To convert literature K<sub>c</sub> values
(expressed as % output / % input):</p>

<div class="eq">K<sub>p</sub> = |K<sub>c</sub> / 100| / PV_span</div>

<!-- ================================================================== -->
<h2>10. Operating Cost Calculation</h2>

<div class="new"><b>New:</b> Real-time operating cost metrics are computed from the Downs &amp; Vogel /
Bathelt &amp; Ricker cost model and published to the data store each scan cycle.</div>

<h3>Cost Tags</h3>
<table>
<tr><th>Tag</th><th>ISA Tag</th><th>Description</th><th>Units</th></tr>
<tr><td><code>operating_cost_per_hr</code></td><td>KI601</td><td>Operating cost (time-based)</td><td>$/hr</td></tr>
<tr><td><code>operating_cost_per_kmol</code></td><td>KI602</td><td>Operating cost (product-based)</td><td>ct/kmol</td></tr>
<tr><td><code>product_rate_molar</code></td><td>FI603</td><td>Molar product rate</td><td>kmol/hr</td></tr>
</table>

<h3>Cost Formula</h3>
<p>The operating cost is calculated from four components:</p>

<div class="eq">Cost = Compressor + Steam + Purge_Loss &minus; Product_Credit</div>

<table>
<tr><th>Component</th><th>Formula</th></tr>
<tr><td>Compressor power</td><td><code>0.0536 &times; xmeas_20</code> (kW)</td></tr>
<tr><td>Stripper steam</td><td><code>0.0318 &times; xmeas_19</code> (kg/hr)</td></tr>
<tr><td>Purge losses</td><td><code>xmeas_10 &times; 0.44791 &times; &sum;(MW<sub>i</sub> &times; purge_comp<sub>i</sub>)</code></td></tr>
<tr><td>Product credit</td><td><code>prate &times; &sum;(price<sub>i</sub> &times; product_comp<sub>i</sub>)</code></td></tr>
</table>

<p>where <code>prate = 211.3 &times; yy[45] / 46.534</code> is the molar product rate (kmol/hr)
from state vector element 46 (1-indexed Fortran convention).</p>

<div class="note"><b>Note:</b> The cost per kmol product (<code>operating_cost_per_kmol</code>) is
the primary economic KPI. Lower values indicate more efficient operation. Typical steady-state
value is approximately 24&ndash;26 ct/kmol under Mode 1 conditions.</div>

<!-- ================================================================== -->
<h2>11. SP Ramping</h2>

<div class="new"><b>New:</b> The simulation engine supports gradual setpoint ramping for smooth
controller setpoint transitions. This avoids step changes that can upset the process.</div>

<h3>Usage</h3>
<p>SP ramps can be initiated via the external write interface:</p>

<pre>store.queue_write("sim.ramp.TIC109", {"target": 122.0, "duration": 300})</pre>

<p>This ramps the TIC109 setpoint linearly from its current value to 122.0&deg;C over
300 seconds (5 minutes).</p>

<h3>Parameters</h3>
<table>
<tr><th>Parameter</th><th>Description</th><th>Default</th></tr>
<tr><td><code>target</code></td><td>Target setpoint value (engineering units)</td><td>&mdash;</td></tr>
<tr><td><code>duration</code></td><td>Ramp duration in seconds</td><td>300</td></tr>
</table>

<p>Multiple ramps can run simultaneously on different controllers. Each ramp updates the
corresponding <code>ctrl.&lt;tag&gt;.SP</code> value linearly each scan cycle until the
target is reached.</p>

<!-- ================================================================== -->
<h2>12. References</h2>

<table>
<tr><th>#</th><th>Reference</th></tr>
<tr><td>1</td><td>Downs, J.J. and Vogel, E.F. (1993). &ldquo;A plant-wide industrial process
    control problem.&rdquo; <i>Computers &amp; Chemical Engineering</i>, 17(3), 245&ndash;255.
    &mdash; <b>The original TE challenge paper.</b></td></tr>
<tr><td>2</td><td>Ricker, N.L. (1996). &ldquo;Decentralized control of the Tennessee Eastman
    Challenge Process.&rdquo; <i>Journal of Process Control</i>, 6(4), 205&ndash;221.
    &mdash; 12-loop decentralized PID strategy.</td></tr>
<tr><td>3</td><td>Lyman, P.R. and Georgakis, C. (1995). &ldquo;Plant-wide control of the
    Tennessee Eastman problem.&rdquo; <i>Computers &amp; Chemical Engineering</i>,
    19(3), 321&ndash;331.</td></tr>
<tr><td>4</td><td>Skogestad, S. (2000). &ldquo;Plantwide control: The search for the
    self-optimizing control structure.&rdquo; <i>Journal of Process Control</i>,
    10(5), 487&ndash;507. &mdash; Self-optimizing control theory applied to TE.</td></tr>
<tr><td>5</td><td>Larsson, T. and Skogestad, S. (2000). &ldquo;Plantwide control &mdash;
    a review and a new design procedure.&rdquo; <i>Modeling, Identification and Control</i>,
    21(4), 209&ndash;240.</td></tr>
<tr><td>6</td><td>McAvoy, T.J. and Ye, N. (1994). &ldquo;Base control for the Tennessee
    Eastman problem.&rdquo; <i>Computers &amp; Chemical Engineering</i>, 18(5),
    383&ndash;413.</td></tr>
<tr><td>7</td><td>Bathelt, A., Ricker, N.L. and Jelali, M. (2015). &ldquo;Revision of
    the Tennessee Eastman Process Model.&rdquo; <i>IFAC-PapersOnLine</i>, 48(8),
    309&ndash;314. &mdash; Extended IDVs 21&ndash;28, operating cost model, updated DLL.</td></tr>
</table>

<hr>
<p style="text-align:center; color:#888; font-size:9pt;">
Tennessee Eastman Process Simulator &mdash; Comprehensive Reference
</p>
"""


CUMENE_HOTOIL_HELP_HTML = """
<style>
body { font-family: Segoe UI, Arial, sans-serif; background: #F5F5F5; }
h1 { color: #1A1A1A; border-bottom: 2px solid #4169E1; padding-bottom: 6px; }
h2 { color: #333; margin-top: 28px; border-bottom: 1px solid #CCC;
     padding-bottom: 4px; }
h3 { color: #4169E1; margin-top: 16px; }
h4 { color: #505050; margin-top: 12px; }
table { border-collapse: collapse; margin: 8px 0; width: 100%; }
th, td { border: 1px solid #CCC; padding: 4px 8px; text-align: left; }
th { background: #E0E0E0; }
code { background: #E8E8E8; padding: 1px 4px; border-radius: 3px;
       font-family: Consolas, monospace; }
pre { background: #E8E8E8; padding: 8px 12px; border-radius: 4px;
      font-family: Consolas, monospace; font-size: 9pt; overflow-x: auto;
      border: 1px solid #CCC; }
.eq { background: #F0F0F0; border: 1px solid #DDD; padding: 6px 12px;
      border-radius: 4px; margin: 6px 0; font-family: Consolas, monospace;
      font-size: 9.5pt; }
.note { background: #FFFDE7; border-left: 3px solid #DAA520; padding: 6px 10px;
        margin: 6px 0; font-size: 9.5pt; }
.new { background: #E8F5E9; border-left: 3px solid #2E8B2E; padding: 6px 10px;
       margin: 6px 0; font-size: 9.5pt; }
</style>

<h1>Cumene Hot Oil Heater Simulator &mdash; Process Reference</h1>

<!-- ================================================================== -->
<h2>1. Process Overview</h2>

<h3>Hot Oil Heater System</h3>
<ul>
<li><b>Type:</b> Cabin-type fired heater with 2-pass coil arrangement</li>
<li><b>Heat transfer fluid:</b> Therminol 66 (Eastman Chemical)</li>
<li><b>Service:</b> Provides heat to 8 exchangers in a cumene production plant</li>
<li><b>Hot oil supply temperature:</b> 665&deg;F</li>
<li><b>Hot oil return temperature:</b> ~477&deg;F (mixed from 8 HXs)</li>
<li><b>Total circulation rate:</b> ~171,000 BPD</li>
<li><b>Design absorbed duty:</b> ~107 MMBTU/hr</li>
<li><b>Design fired duty:</b> ~126 MMBTU/hr (at 85% efficiency)</li>
<li><b>Model:</b> 34-state ODE system with 0.1 s integration time step</li>
<li><b>Integrator:</b> Euler forward with 5 micro-steps per scan</li>
<li><b>Control:</b> Open-loop &mdash; all control via FBD strategy designer</li>
</ul>

<h3>34 State Variables</h3>
<table>
<tr><th>Index</th><th>Symbol</th><th>Description</th><th>Units</th></tr>
<tr><td>0&ndash;1</td><td>T_fluid_1, T_metal_1</td><td>Pass 1 oil outlet &amp; tube metal temps</td><td>&deg;F</td></tr>
<tr><td>2&ndash;3</td><td>T_fluid_2, T_metal_2</td><td>Pass 2 oil outlet &amp; tube metal temps</td><td>&deg;F</td></tr>
<tr><td>4&ndash;5</td><td>T_gas_z1, T_gas_z2</td><td>Firebox gas temps (burner &amp; bridgewall zones)</td><td>K</td></tr>
<tr><td>6&ndash;7</td><td>T_refr_int, T_refr_cold</td><td>Refractory intermediate &amp; cold face temps</td><td>K</td></tr>
<tr><td>8</td><td>T_conv_out</td><td>Convection section oil outlet temp</td><td>&deg;F</td></tr>
<tr><td>9</td><td>T_flue_out</td><td>Flue gas stack temperature</td><td>K</td></tr>
<tr><td>10</td><td>P_draft</td><td>Firebox draft pressure</td><td>Pa</td></tr>
<tr><td>11</td><td>P_fuel_gas</td><td>Fuel gas header pressure</td><td>Pa</td></tr>
<tr><td>12&ndash;15</td><td>valve_fuel, valve_air,<br/>valve_damper, valve_pump</td><td>Heater control valves &amp; pump speed</td><td>0&ndash;1</td></tr>
<tr><td>16</td><td>T_supply</td><td>Hot oil supply header temperature</td><td>&deg;F</td></tr>
<tr><td>17</td><td>T_return</td><td>Hot oil return header temperature</td><td>&deg;F</td></tr>
<tr><td>18&ndash;25</td><td>T_hx_out_0..7</td><td>8 HX hot oil outlet temperatures</td><td>&deg;F</td></tr>
<tr><td>26&ndash;33</td><td>valve_hx_0..7</td><td>8 HX flow control valve positions</td><td>0&ndash;1</td></tr>
</table>

<!-- ================================================================== -->
<h2>2. Therminol 66 Properties</h2>

<p>Therminol 66 is a modified terphenyl heat transfer fluid designed for use in
non-pressurized, indirectly-heated systems up to 345&deg;C (653&deg;F). It
remains liquid throughout the operating range (no two-phase modeling needed).</p>

<h3>Property Correlations</h3>
<table>
<tr><th>Property</th><th>Correlation</th><th>Range</th></tr>
<tr><td>Density &rho;</td><td><code>&rho;(T) = 1020.6 &minus; 0.5808&times;T<sub>C</sub> &minus; 2.022e-4&times;T<sub>C</sub>&sup2;</code> kg/m&sup3;</td>
    <td>~1021 @ 0&deg;C, ~817 @ 345&deg;C</td></tr>
<tr><td>Specific heat C<sub>p</sub></td><td><code>C<sub>p</sub>(T) = 1496 + 3.313&times;T<sub>C</sub></code> J/(kg&middot;K)</td>
    <td>~1496 @ 0&deg;C, ~2639 @ 345&deg;C</td></tr>
<tr><td>Viscosity &mu;</td><td><code>ln(&mu;) = &minus;2.165 + 816/(T<sub>C</sub>+136)</code> cP</td>
    <td>~100 cP @ 0&deg;C, ~0.5 cP @ 345&deg;C</td></tr>
<tr><td>Thermal cond. k</td><td><code>k(T) = 0.1180 &minus; 1.50e-4&times;T<sub>C</sub></code> W/(m&middot;K)</td>
    <td>~0.118 @ 0&deg;C, ~0.066 @ 345&deg;C</td></tr>
<tr><td>Film HTC h<sub>i</sub></td><td>Dittus-Boelter: <code>Nu = 0.023 Re<sup>0.8</sup> Pr<sup>0.4</sup></code></td>
    <td>Turbulent in-tube correlation</td></tr>
</table>

<div class="note"><b>Note:</b> Maximum recommended film temperature is 716&deg;F (380&deg;C).
Exceeding this causes accelerated fluid degradation.</div>

<!-- ================================================================== -->
<h2>3. Fired Heater Model</h2>

<h3>Heater Geometry</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Firebox dimensions (L &times; W &times; H)</td><td>16 &times; 6 &times; 12 m</td></tr>
<tr><td>Number of coil passes</td><td>2 (series, radiant section)</td></tr>
<tr><td>Tubes per pass</td><td>48 (96 total)</td></tr>
<tr><td>Tube OD / ID</td><td>4.5" / 3.854" (Sch 40, 5Cr-0.5Mo)</td></tr>
<tr><td>Radiant tube length</td><td>16 m per tube</td></tr>
<tr><td>Convection bank</td><td>20 tubes/row &times; 8 rows, 3.5" OD, finned</td></tr>
<tr><td>Stack</td><td>3.0 m dia &times; 40 m height</td></tr>
<tr><td>Burners</td><td>12 &times; 12.5 MW each (150 MW total capacity)</td></tr>
</table>

<h3>Firebox Model (Two-Zone)</h3>
<p>The firebox is split into two vertical zones:</p>
<ul>
<li><b>Zone 1 (lower, burner zone):</b> All combustion heat is released here.
    Pass 1 tubes are heated by zone 1 radiation.</li>
<li><b>Zone 2 (upper, bridgewall zone):</b> Receives inter-zone radiation from
    zone 1. Pass 2 tubes are heated by zone 2 radiation. Flue gas exits to the
    convection section.</li>
</ul>

<h4>Energy Balance (per zone)</h4>
<div class="eq">C<sub>zone</sub> &middot; dT<sub>gas</sub>/dt = Q<sub>release</sub>
&minus; Q<sub>rad</sub> &minus; Q<sub>interzone</sub> &minus; Q<sub>ambient</sub>
&minus; Q<sub>flue,out</sub></div>

<h4>Radiant Heat Transfer (Lobo-Evans Method)</h4>
<div class="eq">Q<sub>rad</sub> = &sigma; &middot; F &middot; &epsilon;<sub>tube</sub>
&middot; (T<sub>gas</sub><sup>4</sup> &minus; T<sub>tube</sub><sup>4</sup>)</div>
<p>Exchange factor F accounts for gas emissivity (CO<sub>2</sub> + H<sub>2</sub>O bands),
refractory re-radiation, and tube geometry.</p>

<h3>Refractory Model (3-Layer)</h3>
<table>
<tr><th>Layer</th><th>Thickness</th><th>Response Time</th></tr>
<tr><td>Hot face (castable)</td><td>3 mm</td><td>~5 s</td></tr>
<tr><td>Intermediate (firebrick)</td><td>20 mm</td><td>~2 min</td></tr>
<tr><td>Cold face (insulation)</td><td>75 mm</td><td>~1 hr</td></tr>
</table>

<h3>Convection Section</h3>
<p>Counter-current heat exchange between flue gas and hot oil. The oil enters
the convection section at the return temperature (~477&deg;F) and is preheated
before entering the radiant coil. The convection section absorbs ~20% of total
duty.</p>

<h3>Combustion</h3>
<table>
<tr><th>Parameter</th><th>Design Value</th></tr>
<tr><td>Fuel gas</td><td>70% CH<sub>4</sub>, 15% C<sub>2</sub>H<sub>6</sub>,
    5% C<sub>3</sub>H<sub>8</sub>, 8% H<sub>2</sub>, 2% N<sub>2</sub></td></tr>
<tr><td>Excess air</td><td>15%</td></tr>
<tr><td>Stack O<sub>2</sub></td><td>~3% (design), ~5% (initial)</td></tr>
<tr><td>Stack temperature</td><td>~500&deg;F</td></tr>
<tr><td>Efficiency</td><td>~85% (absorbed / fired)</td></tr>
</table>

<!-- ================================================================== -->
<h2>4. Heat Exchanger Network</h2>

<p>The hot oil system serves 8 parallel heat exchangers in the cumene plant.
Each HX is modeled as a counter-current exchanger with a constant-temperature
process side (reboiler or heater duty).</p>

<h3>HX Configuration Table</h3>
<table>
<tr><th>Idx</th><th>Tag</th><th>Service</th><th>Flow (BPD)</th><th>T<sub>in</sub> (&deg;F)</th>
    <th>T<sub>out</sub> (&deg;F)</th><th>Duty (MMBTU/hr)</th><th>Process T (&deg;F)</th></tr>
<tr><td>0</td><td>EA442</td><td>Recycle Col. Reboiler</td>
    <td>15,299</td><td>537</td><td>410</td><td>20.82</td><td>380</td></tr>
<tr><td>1</td><td>EA451</td><td>#1 Cumene Col. Reboiler</td>
    <td>35,790</td><td>665</td><td>496</td><td>33.91</td><td>470</td></tr>
<tr><td>2</td><td>EA428</td><td>#2 Cumene Col. Reboiler</td>
    <td>8,851</td><td>665</td><td>431</td><td>9.09</td><td>400</td></tr>
<tr><td>3</td><td>EA426</td><td>PIPB Reboiler</td>
    <td>5,165</td><td>663</td><td>478</td><td>5.33</td><td>450</td></tr>
<tr><td>4</td><td>EA462</td><td>#1 Rectifier Col. Reboiler</td>
    <td>68,679</td><td>537</td><td>484</td><td>~25.7</td><td>460</td></tr>
<tr><td>5</td><td>EA434</td><td>Transalky Feed Heater</td>
    <td>862</td><td>440</td><td>326</td><td>~0.66</td><td>300</td></tr>
<tr><td>6</td><td>EA464</td><td>#1 Rectifier Col. Preheater</td>
    <td>36,529</td><td>495</td><td>425</td><td>~17.7</td><td>400</td></tr>
<tr><td>7</td><td>EA453</td><td>Reactor Shutdown Stripping</td>
    <td>standby</td><td>&mdash;</td><td>&mdash;</td><td>0</td><td>&mdash;</td></tr>
</table>

<h3>HX Energy Balance</h3>
<div class="eq">M<sub>oil</sub> &middot; C<sub>p</sub> &middot; dT<sub>ho</sub>/dt
= &#7745;<sub>oil</sub> &middot; C<sub>p</sub> &middot; (T<sub>hi</sub> &minus; T<sub>ho</sub>)
&minus; UA &middot; LMTD</div>
<p>where LMTD is computed for constant process-side temperature:</p>
<div class="eq">LMTD = [(T<sub>hi</sub> &minus; T<sub>proc</sub>) &minus;
(T<sub>ho</sub> &minus; T<sub>proc</sub>)] / ln[(T<sub>hi</sub> &minus; T<sub>proc</sub>)
/ (T<sub>ho</sub> &minus; T<sub>proc</sub>)]</div>

<h3>Flow Distribution</h3>
<p>Total pump flow is distributed among 8 parallel HX paths based on each valve&rsquo;s
effective Cv (equal-percentage characteristic with R=15). Closing one valve increases
flow to the other HXs:</p>
<div class="eq">flow<sub>i</sub> = total_flow &times;
(Cv<sub>max,i</sub> &times; R<sup>(x<sub>i</sub>&minus;1)</sup>) /
&Sigma;(Cv<sub>max,j</sub> &times; R<sup>(x<sub>j</sub>&minus;1)</sup>)</div>

<!-- ================================================================== -->
<h2>5. Control Strategy</h2>

<p>The heater runs <b>open-loop</b> by default. All control is implemented via
the FBD strategy designer (<b>View &rarr; Control Designer</b>, Ctrl+K). Suggested
control loops:</p>

<table>
<tr><th>Tag</th><th>PV</th><th>MV</th><th>Action</th><th>Description</th></tr>
<tr><td>TIC400</td><td>T_supply (665&deg;F)</td><td>valve_fuel</td>
    <td>Reverse</td><td>Heater outlet temp control</td></tr>
<tr><td>AIC410</td><td>O2_pct (3%)</td><td>valve_air</td>
    <td>Direct</td><td>Stack O<sub>2</sub> control</td></tr>
<tr><td>PIC410</td><td>P_draft_inH2O</td><td>valve_damper</td>
    <td>Reverse</td><td>Firebox draft control</td></tr>
<tr><td>FIC440</td><td>flow_hx_0</td><td>valve_hx_0</td>
    <td>Direct</td><td>EA442 flow control</td></tr>
<tr><td>FIC441</td><td>flow_hx_1</td><td>valve_hx_1</td>
    <td>Direct</td><td>EA451 flow control</td></tr>
<tr><td>FIC442</td><td>flow_hx_2</td><td>valve_hx_2</td>
    <td>Direct</td><td>EA428 flow control</td></tr>
<tr><td>FIC443</td><td>flow_hx_3</td><td>valve_hx_3</td>
    <td>Direct</td><td>EA426 flow control</td></tr>
<tr><td>FIC444</td><td>flow_hx_4</td><td>valve_hx_4</td>
    <td>Direct</td><td>EA462 flow control</td></tr>
<tr><td>FIC445</td><td>flow_hx_5</td><td>valve_hx_5</td>
    <td>Direct</td><td>EA434 flow control</td></tr>
<tr><td>FIC446</td><td>flow_hx_6</td><td>valve_hx_6</td>
    <td>Direct</td><td>EA464 flow control</td></tr>
</table>

<div class="note"><b>Strategy wiring pattern:</b> AI (PV tag) &rarr; PID &rarr;
SCALER (0&ndash;1 &rarr; 0&ndash;100%) &rarr; AO (valve tag). Include BKCAL
wires from AO &rarr; SCALER &rarr; PID for bumpless transfer.</div>

<!-- ================================================================== -->
<h2>6. Disturbances</h2>

<table>
<tr><th>Tag</th><th>Description</th><th>Range</th></tr>
<tr><td>ambient_temp_offset</td><td>Ambient temperature offset from 77&deg;F</td>
    <td>&minus;50 to +50 &deg;F</td></tr>
<tr><td>proc_temp_offset_0..7</td><td>Per-HX process side temp offset</td>
    <td>&minus;50 to +50 &deg;F</td></tr>
</table>

<p>Process temperature offsets simulate changes in column/reactor operating conditions
that change the heat duty demand on each exchanger.</p>

<!-- ================================================================== -->
<h2>7. Dashboard Layout</h2>

<h3>Row 1 &mdash; Hot Oil Loop</h3>
<p>T<sub>supply</sub> (&deg;F), T<sub>return</sub> (&deg;F), Efficiency (%), Pump Flow (BPD)</p>

<h3>Row 2 &mdash; Heater</h3>
<p>Fired Duty (MMBTU/hr), Firebox Temp (&deg;F), Stack Temp (&deg;F), Draft (inH<sub>2</sub>O)</p>

<h3>Row 3 &mdash; Combustion</h3>
<p>O<sub>2</sub> (%), CO (ppm), Fuel Rate (kg/s), Air Rate (kg/s)</p>

<h3>Row 4 &mdash; Heat Delivered</h3>
<p>Q<sub>total</sub> (MMBTU/hr), EA451 Duty, EA462 Duty, EA442 Duty</p>

<h3>Valve Bars (12)</h3>
<p>Fuel, Air, Damper, Pump, EA442, EA451, EA428, EA426, EA462, EA434, EA464, EA453</p>

<!-- ================================================================== -->
<h2>8. Design Operating Point</h2>

<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Supply temperature</td><td>665&deg;F</td></tr>
<tr><td>Return temperature</td><td>~477&deg;F</td></tr>
<tr><td>Total pump flow</td><td>171,175 BPD</td></tr>
<tr><td>Fired duty</td><td>~126 MMBTU/hr</td></tr>
<tr><td>Absorbed duty</td><td>~107 MMBTU/hr</td></tr>
<tr><td>Thermal efficiency</td><td>~85%</td></tr>
<tr><td>Firebox zone 1 temp</td><td>~1650&deg;F</td></tr>
<tr><td>Firebox zone 2 temp</td><td>~1350&deg;F</td></tr>
<tr><td>Stack temperature</td><td>~500&deg;F</td></tr>
<tr><td>Draft</td><td>&minus;0.30 inH<sub>2</sub>O</td></tr>
<tr><td>Excess air</td><td>15%</td></tr>
</table>

<!-- ================================================================== -->
<h2>9. Tag Conventions</h2>

<table>
<tr><th>Category</th><th>Pattern</th><th>Examples</th></tr>
<tr><td>Temperatures</td><td>T_supply, T_return, T_hx_<i>n</i></td><td>T_supply, T_hx_1</td></tr>
<tr><td>Energy</td><td>Q_fired, Q_absorbed, Q_hx_<i>n</i></td><td>Q_fired, Q_hx_4</td></tr>
<tr><td>Combustion</td><td>O2_pct, CO_ppm, excess_air_pct</td><td>O2_pct</td></tr>
<tr><td>Flows</td><td>pump_flow_bpd, flow_hx_<i>n</i></td><td>flow_hx_1</td></tr>
<tr><td>Valves</td><td>valve_fuel, valve_hx_<i>n</i></td><td>valve_hx_0</td></tr>
<tr><td>Disturbances</td><td>ambient_temp_offset, proc_temp_offset_<i>n</i></td><td>proc_temp_offset_1</td></tr>
<tr><td>Controllers</td><td>ctrl.&lt;tag&gt;.PV / .SP / .OUT</td><td>ctrl.TIC400.PV</td></tr>
</table>

<!-- ================================================================== -->
<h2>10. Verification Checklist</h2>

<ol>
<li><b>Steady-state:</b> Supply temp should settle to ~665&deg;F with proper fuel valve position.</li>
<li><b>Energy balance:</b> Sum of HX duties &asymp; absorbed duty; fired duty / absorbed &asymp; 85% efficiency.</li>
<li><b>Flow balance:</b> Total pump flow = sum of 8 HX flows.</li>
<li><b>Disturbance response:</b> Step fuel valve, verify COT responds with first-order + dead time characteristic.</li>
<li><b>HX isolation:</b> Close one HX valve, verify flow redistributes and supply temp rises (less duty absorbed).</li>
</ol>

<hr>
<p style="text-align:center; color:#888; font-size:9pt;">
Cumene Hot Oil Heater Simulator &mdash; Process Reference
</p>
"""

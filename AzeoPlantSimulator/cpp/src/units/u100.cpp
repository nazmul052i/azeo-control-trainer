#include "azeocore/units/u100.hpp"

#include <algorithm>
#include <cmath>

namespace azeocore::units {

FeedSection::FeedSection(TagDatabase& db_, ProcessBus& bus_, double dt_)
    : ProcessUnit(db_, bus_, dt_, "U100", "Feed surge drum D1 and charge pumps"),
      mov_a("MOV-1001A", 30.0, 100.0), mov_b("MOV-1001B", 30.0, 0.0),
      motor_a("P-101A", 165.0, 1.0, 3.0, true), motor_b("P-101B", 165.0, 1.0, 3.0, false),
      pump_a("P-101A", 280.0, 360.0, 25.0), pump_b("P-101B", 280.0, 360.0, 25.0),
      fcv1001("FCV-1001", 260, ValveChar::EqualPercent, 6),
      fcv1002("FCV-1002", 70, ValveChar::Linear, 4, false),
      lcv1001("LCV-1001", 90, ValveChar::Linear, 6),
      level(55.0, 0.0, 100.0), charge_flow(1.5, 0.0), minflow(1.2, 0.0), bearing_temp(120.0, 45.0),
      tx_level("LT-1001", 0, 100, 1.5, 0.25), tx_flow("FT-1001", 0, 200, 0.6, 0.45),
      tx_fresh("FT-1004", 0, 150, 0.6, 0.45), tx_fresh_p("PT-1005", 0, 10, 0.5, 0.3),
      tx_fresh_t("TT-1003", 0, 100, 2.0, 0.2), tx_fresh_z("AT-1001", 0, 50, 30.0, 0.5),
      tx_pdis("PT-1002", 0, 30, 0.4, 0.2), tx_psuc_a("PT-1003", 0, 10, 0.4, 0.2) {
    // tags, in the Python build() order
    LT1001 = &ai("LT-1001", "D1 feed surge drum level", "%", 0, 100, 55.0);
    PT1001 = &ai("PT-1001", "D1 vapour space pressure", "barg", 0, 10, 2.5);
    PT1002 = &ai("PT-1002", "P-101 discharge header pressure", "barg", 0, 30);
    PT1003 = &ai("PT-1003", "P-101A suction pressure", "barg", 0, 10);
    PT1004 = &ai("PT-1004", "P-101B suction pressure", "barg", 0, 10);
    PDT1001 = &ai("PDT-1001", "P-101A suction strainer differential", "bar", 0, 2);
    TT1001 = &ai("TT-1001", "D1 outlet temperature", "degC", 0, 200, 62.0);
    TT1002 = &ai("TT-1002", "P-101A bearing temperature", "degC", 0, 150, 45.0);
    FT1001 = &ai("FT-1001", "Charge flow from D1", "m3/h", 0, 300);
    FT1002 = &ai("FT-1002", "T1 bottoms recycle to D1", "m3/h", 0, 180);
    FT1003 = &ai("FT-1003", "P-101 minimum flow recycle", "m3/h", 0, 60);
    FT1004 = &ai("FT-1004", "Fresh feed flow from battery limit", "m3/h", 0, 150, 66.2);
    PT1005 = &ai("PT-1005", "Fresh feed supply pressure", "barg", 0, 10, 6.0);
    TT1003 = &ai("TT-1003", "Fresh feed temperature", "degC", 0, 100, 38.0);
    AT1001 = &ai("AT-1001", "Fresh feed light key content", "mol%", 0, 50, 12.0);
    ST1001 = &ai("ST-1001", "P-101A speed feedback", "%", 0, 100);
    IT1001 = &ai("IT-1001", "P-101A motor current", "A", 0, 200);
    IT1002 = &ai("IT-1002", "P-101B motor current", "A", 0, 200);
    VT1001 = &ai("VT-1001", "P-101A vibration", "micron", 0, 100, 12.0);
    DT1001 = &ai("DT-1001", "Charge density", "kg/m3", 600, 900, DENSITY);
    ZT1001 = &ai("ZT-1001", "FCV-1001 position feedback", "%", 0, 100);
    ao("FCV-1001", "D1 charge flow control valve", "%", 0.0, 100.0, 68.0);
    ao("FCV-1002", "P-101 minimum flow recycle valve", "%", 0.0, 100.0, 0.0);
    ao("LCV-1001", "D1 off-specification import valve");
    ao("SC-1001", "P-101A VFD speed reference", "%", 0.0, 100.0, 100.0);
    add_pump_io("P101A", "P-101A", true);
    add_pump_io("P101B", "P-101B", false);
    add_mov_io("MOV1001A", "MOV-1001A");
    add_mov_io("MOV1001B", "MOV-1001B");
    di("ZSO-HV1001", "HV-1001 D1 drain manual valve open limit", "Not open", "Open");
    di("ZSC-HV1001", "HV-1001 D1 drain manual valve closed limit", "Not closed", "Closed", true);
    di("LSLL-1001", "D1 level low low", "Normal", "Tripped");
    di("LSHH-1001", "D1 level high high", "Normal", "Tripped");
    di("ZSO-XV1001", "XV-1001 charge SDV open limit", "Not open", "Open", true);
    di("ZSC-XV1001", "XV-1001 charge SDV closed limit", "Not closed", "Closed");
    do_("XY-XV1001-OPN", "XV-1001 charge SDV open command", "Close", "Open", true);
    mov_recycle = std::make_unique<MovPackage>(*this, "MOV-1002", "D1 recycle inlet isolation", 25.0, 100.0);
    di("ZSO-HV1002", "HV-1002 open limit switch", "Not open", "Open", false);
    di("ZSC-HV1002", "HV-1002 closed limit switch", "Not closed", "Closed", true);

    // dynamic members, by the Python attribute names
    dyn("mov_a", mov_a); dyn("mov_b", mov_b); dyn("mov_recycle", *mov_recycle);
    dyn("motor_a", motor_a); dyn("motor_b", motor_b); dyn("pump_a", pump_a); dyn("pump_b", pump_b);
    dyn("fcv1001", fcv1001); dyn("fcv1002", fcv1002); dyn("lcv1001", lcv1001);
    dyn("level", level); dyn("charge_flow", charge_flow); dyn("minflow", minflow); dyn("bearing_temp", bearing_temp);
    dyn("tx_level", tx_level); dyn("tx_flow", tx_flow); dyn("tx_fresh", tx_fresh); dyn("tx_fresh_p", tx_fresh_p); dyn("tx_fresh_t", tx_fresh_t); dyn("tx_fresh_z", tx_fresh_z); dyn("tx_pdis", tx_pdis); dyn("tx_psuc_a", tx_psuc_a);

    add_malfunction("MF-001", "FCV-1001", "Control valve stiction", "Valve", "Stick band", 0, 10,
                    [this](bool a, double v) { fcv1001.stiction = a ? v : 0.0; });
    add_malfunction("MF-002", "FCV-1001", "Control valve hysteresis", "Valve", "Deadband", 0, 10,
                    [this](bool a, double v) { fcv1001.hysteresis = a ? v : 0.0; });
    add_malfunction("MF-007", "MOV-1001A", "MOV fails to open on command", "MOV", "", 0, 1,
                    [this](bool a, double) { mov_a.fail_to_open = a; });
    add_malfunction("MF-008", "MOV-1001A", "MOV torque switch trips mid-travel", "MOV", "Trip at travel", 0, 100,
                    [this](bool a, double v) { if (a) mov_a.torque_trip_at = v; else mov_a.torque_trip_at.reset(); });
    add_malfunction("MF-009", "MOV-1001A", "MOV open limit switch fails to make", "MOV", "", 0, 1,
                    [this](bool a, double) { mov_a.open_limit_faulty = a; });
    add_malfunction("MF-010", "MOV-1001A", "MOV slow travel", "MOV", "Travel factor", 1, 6,
                    [this](bool a, double v) { mov_a.slow_travel_factor = a ? v : 1.0; });
    add_malfunction("MF-011", "MOV-1001B", "MOV drifts closed without command", "MOV", "", 0, 1,
                    [this](bool a, double) { mov_b.drifts_closed = a; });
    add_malfunction("MF-012", "FT-1001", "Transmitter drift", "Transmitter", "Drift per hour", -5, 5,
                    [this](bool a, double v) { tx_flow.drift_pct_per_hour = a ? v : 0.0; });
    add_malfunction("MF-013", "FT-1001", "Transmitter noise increase", "Transmitter", "Sigma", 0, 5,
                    [this](bool a, double v) { tx_flow.noise_sigma_pct = a ? v : 0.45; });
    add_malfunction("MF-014", "LT-1001", "Transmitter frozen at last value", "Transmitter", "", 0, 1,
                    [this](bool a, double) { tx_level.failure = a ? TxFailure::Frozen : TxFailure::None; });
    add_malfunction("MF-021", "P-101A", "Pump trip on overload", "Rotating", "", 0, 1,
                    [this](bool a, double) { motor_a.trip_on_overload = a; });
    add_malfunction("MF-022", "P-101A", "Pump impeller wear", "Rotating", "Head loss", 0, 40,
                    [this](bool a, double v) { pump_a.wear_pct = a ? v : 0.0; });
    add_malfunction("MF-023", "P-101A", "VFD communication fault", "Rotating", "", 0, 1,
                    [this](bool a, double) { motor_a.vfd_comms_fault = a; });
    add_malfunction("MF-031", "D1", "Fresh feed rate step", "Process", "Feed rate", 0, 160,
                    [this](bool a, double v) { fresh_feed = a ? v : 66.2; });
    add_malfunction("MF-035", "D1", "Fresh feed temperature step", "Process", "Feed temperature", 10, 80,
                    [this](bool a, double v) { fresh_temperature = a ? v : 38.0; });
    add_malfunction("MF-036", "D1", "Fresh feed light key step", "Process", "Light key mol%", 0, 30,
                    [this](bool a, double v) { fresh_lightfrac = a ? v / 100.0 : 0.12; });
    add_malfunction("MF-040", "D1", "Fresh feed supply pressure step", "Process", "Supply pressure", 1, 10,
                    [this](bool a, double v) { fresh_pressure = a ? v : 6.0; });
}

void FeedSection::add_pump_io(const std::string& base, const std::string& tag, bool vfd) {
    di("XS-" + base + "-RUN", tag + " running feedback", "Stopped", "Running");
    di("XS-" + base + "-FLT", tag + " fault or trip", "Healthy", "Faulted");
    di("XS-" + base + "-AVL", tag + " available and in remote", "Not avail", "Available", true);
    if (vfd) di("XS-" + base + "-VFD", tag + " VFD healthy", "Fault", "Healthy", true);
    do_("XY-" + base + "-STR", tag + " start command", "Idle", "Start");
    do_("XY-" + base + "-STP", tag + " stop command", "Idle", "Stop");
}

void FeedSection::add_mov_io(const std::string& base, const std::string& tag) {
    di("ZSO-" + base, tag + " open limit switch", "Not open", "Open");
    di("ZSC-" + base, tag + " closed limit switch", "Not closed", "Closed");
    di("XS-" + base + "-TRQ", tag + " torque or thermal overload trip", "Healthy", "Tripped");
    di("XS-" + base + "-AVL", tag + " actuator in remote", "Local", "Remote", true);
    do_("XY-" + base + "-OPN", tag + " open command from control room", "Idle", "Open");
    do_("XY-" + base + "-CLS", tag + " close command from control room", "Idle", "Close");
}

static inline double b2d(bool b) { return b ? 1.0 : 0.0; }

void FeedSection::step(double dt) {
    const bool air = bus_get("air_failure") != 0.0;
    mov_a.remote = !mov_a_local;
    mov_b.remote = !mov_b_local;
    t("XS-MOV1001A-AVL").set(b2d(mov_a.remote && !mov_a.torque_tripped));
    t("XS-MOV1001B-AVL").set(b2d(mov_b.remote && !mov_b.torque_tripped));
    mov_a.command(t("XY-MOV1001A-OPN").effective() != 0.0, t("XY-MOV1001A-CLS").effective() != 0.0);
    mov_b.command(t("XY-MOV1001B-OPN").effective() != 0.0, t("XY-MOV1001B-CLS").effective() != 0.0);
    mov_a.step(dt);
    mov_b.step(dt);
    t("ZSO-MOV1001A").set(b2d(mov_a.zso()));
    t("ZSC-MOV1001A").set(b2d(mov_a.zsc()));
    t("XS-MOV1001A-TRQ").set(b2d(mov_a.torque_tripped));
    t("ZSO-MOV1001B").set(b2d(mov_b.zso()));
    t("ZSC-MOV1001B").set(b2d(mov_b.zsc()));
    t("XS-MOV1001B-TRQ").set(b2d(mov_b.torque_tripped));

    const double level_head = STATIC_HEAD * (level.y / 100.0);
    const double p_drum = PT1001->value;
    const double flow_prev = charge_flow.y + minflow.y;
    auto suction = [&](const MotorOperatedValve& mov) {
        const double frac = std::max(mov.flow_fraction(), 1e-3);
        const double q = flow_prev / 150.0;
        // Python: 0.55 * q ** 2 / frac ** 2, the runtime's pow both times
        const double loss = strainer_dp + 0.55 * py_pow(q, 2.0) / py_pow(frac, 2.0);
        return clamp(p_drum + level_head - loss, -0.9, 12.0);
    };
    const double p_suc_a = suction(mov_a);
    const double p_suc_b = suction(mov_b);
    motor_a.command(t("XY-P101A-STR").effective() != 0.0, t("XY-P101A-STP").effective() != 0.0);
    motor_b.command(t("XY-P101B-STR").effective() != 0.0, t("XY-P101B-STP").effective() != 0.0);
    motor_a.step(dt, true, t("SC-1001").effective());
    motor_b.step(dt, true, 100.0);
    const bool primed = level.y > 2.0;
    bool cav_a = pump_a.check_cavitation(p_suc_a, VAPOUR_PRESSURE, motor_a.running);
    bool cav_b = pump_b.check_cavitation(p_suc_b, VAPOUR_PRESSURE, motor_b.running);
    if (!primed) {
        if (motor_a.running) { pump_a.cavitating = true; cav_a = true; }
        if (motor_b.running) { pump_b.cavitating = true; cav_b = true; }
    }
    const bool esd = bus_get("esd_u100") != 0.0;
    xv1001_open = (t("XY-XV1001-OPN").effective() != 0.0) && !esd && !air;
    t("ZSO-XV1001").set(b2d(xv1001_open));
    t("ZSC-XV1001").set(b2d(!xv1001_open));
    fcv1001.step(dt, t("FCV-1001").effective(), air);
    fcv1002.step(dt, t("FCV-1002").effective(), air);
    lcv1001.step(dt, t("LCV-1001").effective(), air);
    double head_a, head_b;
    if (primed) {
        head_a = pump_a.head(flow_prev, motor_a.speed_pct);
        head_b = pump_b.head(flow_prev, motor_b.speed_pct);
        if (cav_a) head_a *= 0.45;
        if (cav_b) head_b *= 0.45;
    } else {
        head_a = head_b = 0.0;
    }
    // Python: max((head_a, p_suc_a), (head_b, p_suc_b), key=head) keeps the first on a tie
    const bool a_wins = head_a >= head_b;
    const double p_dis = (a_wins ? p_suc_a : p_suc_b) + (a_wins ? head_a : head_b) * DENSITY * 9.81 / 1e5;
    const double downstream = bus_get("h1_inlet_pressure");
    const double sg = DENSITY / 1000.0;
    double q_charge = 0.0;
    if (xv1001_open) q_charge = fcv1001.flow(p_dis - downstream, sg);
    const double q_min = fcv1002.flow(p_dis - p_drum, sg);
    charge_flow.step(q_charge, dt);
    minflow.step(q_min, dt);
    t("ZSO-HV1002").set(0.0);
    t("ZSC-HV1002").set(1.0);
    mov_recycle->step(dt);
    const double recycle = bus_get("t1_bottoms_recycle") * mov_recycle->device.position / 100.0;
    const double makeup = lcv1001.flow(4.0, sg);
    const double drain = 55.0 * py_pow(hv1001_position / 100.0, 0.5);
    const double inventory_in = fresh_feed + recycle + makeup + minflow.y;
    const double inventory_out = charge_flow.y + minflow.y + drain;
    const double net = inventory_in - inventory_out;
    const double level_before = level.y;
    level.step(net / (D1_AREA * D1_HEIGHT) * 100.0 / 3600.0, dt);
    record_inventory_balance("D1 liquid inventory", inventory_in, inventory_out, level_before, level.y,
                             D1_AREA * D1_HEIGHT, dt);
    // the calculation trace: hydraulics and the drum's material balance
    tr("p_suc_a", p_suc_a); tr("p_suc_b", p_suc_b); tr("head_a", head_a); tr("head_b", head_b); tr("p_dis", p_dis);
    tr("downstream", downstream); tr("q_charge", q_charge); tr("q_min", q_min);
    tr("fresh_feed", fresh_feed); tr("recycle", recycle); tr("makeup", makeup); tr("drain", drain);
    tr("inventory_in", inventory_in); tr("inventory_out", inventory_out); tr("net", net); tr("level", level.y);
    tr("FCV-1001", fcv1001.position); tr("FCV-1002", fcv1002.position); tr("LCV-1001", lcv1001.position);
    tr("MOV-1001A", mov_a.position); tr("MOV-1001B", mov_b.position); tr("MOV-1002", mov_recycle->device.position);
    tr("speed_a", motor_a.speed_pct); tr("cav_a", cav_a ? 1.0 : 0.0); tr("cav_b", cav_b ? 1.0 : 0.0);
    bus_set("charge_flow", charge_flow.y);
    bus_set("charge_temperature", TT1001->value);
    bus_set("d1_level", level.y);
    bus_set("fresh_lightfrac", fresh_lightfrac);
    { const double v_ = tx_level.step(dt, level.y); LT1001->set(v_, tx_level.quality); }
    { const double v_ = tx_flow.step(dt, charge_flow.y); FT1001->set(v_, tx_flow.quality); }
    { const double v_ = tx_fresh.step(dt, fresh_feed); FT1004->set(v_, tx_fresh.quality); }
    { const double v_ = tx_fresh_p.step(dt, fresh_pressure); PT1005->set(v_, tx_fresh_p.quality); }
    { const double v_ = tx_fresh_t.step(dt, fresh_temperature); TT1003->set(v_, tx_fresh_t.quality); }
    { const double v_ = tx_fresh_z.step(dt, fresh_lightfrac * 100.0); AT1001->set(v_, tx_fresh_z.quality); }
    { const double v_ = tx_pdis.step(dt, p_dis); PT1002->set(v_, tx_pdis.quality); }
    { const double v_ = tx_psuc_a.step(dt, p_suc_a); PT1003->set(v_, tx_psuc_a.quality); }
    PT1004->set(clamp(p_suc_b, 0.0, 10.0));
    {
        const double q = flow_prev / 150.0;
        PDT1001->set(clamp(strainer_dp + 0.55 * py_pow(q, 2.0), 0, 2));
    }
    FT1002->set(recycle);
    FT1003->set(minflow.y);
    ST1001->set(motor_a.speed_pct);
    IT1001->set(motor_a.current());
    IT1002->set(motor_b.current());
    ZT1001->set(fcv1001.position);
    const double vib = 12.0 + (cav_a ? 28.0 : 0.0) + pump_a.wear_pct * 0.6;
    VT1001->set(motor_a.running ? vib : 2.0);
    TT1002->set(bearing_temp.step(45.0 + (motor_a.running ? 35.0 : 0.0) + (cav_a ? 15.0 : 0.0), dt));
    PT1001->set(clamp(2.5 + 0.004 * (level.y - 55.0), 0.5, 8.0));
    const double t_recycle = bus_get("t1_bottoms_temperature");
    const double total_in = std::max(fresh_feed + recycle, 1e-3);
    const double t_mix = (fresh_feed * fresh_temperature
                          + recycle * std::min(t_recycle, 260.0)) / total_in;
    TT1001->set(clamp(t_mix, 10.0, 145.0));
    DT1001->set(clamp(820.0 - 0.62 * TT1001->value, 600.0, 900.0));
    t("XS-P101A-AVL").set(b2d(!local_a && !motor_a.faulted));
    t("XS-P101B-AVL").set(b2d(!local_b && !motor_b.faulted));
    t("XS-P101A-RUN").set(b2d(motor_a.running));
    t("XS-P101A-FLT").set(b2d(motor_a.faulted));
    t("XS-P101A-VFD").set(b2d(motor_a.vfd_healthy));
    t("XS-P101B-RUN").set(b2d(motor_b.running));
    t("XS-P101B-FLT").set(b2d(motor_b.faulted));
    t("ZSO-HV1001").set(b2d(hv1001_position >= 99.0));
    t("ZSC-HV1001").set(b2d(hv1001_position <= 1.0));
    t("LSLL-1001").set(b2d(level.y < 8.0));
    t("LSHH-1001").set(b2d(level.y > 92.0));
}

Value FeedSection::save_state() const {
    Dict d;
    d["level"] = level.y; d["charge"] = charge_flow.y;
    d["mov_a"] = mov_a.position; d["mov_b"] = mov_b.position;
    d["motor_a"] = motor_a.state(); d["motor_b"] = motor_b.state();
    d["hv1001"] = hv1001_position; d["feed"] = fresh_feed;
    d["feed_t"] = fresh_temperature; d["feed_p"] = fresh_pressure; d["feed_z"] = fresh_lightfrac;
    return d;
}

void FeedSection::load_state(const Value& s) {
    level.reset(s.get_number("level", 55.0));
    charge_flow.reset(s.get_number("charge", 0.0));
    mov_a.position = s.get_number("mov_a", 100.0);
    mov_b.position = s.get_number("mov_b", 0.0);
    if (s.get("motor_a").is_dict()) motor_a.apply_state(s.get("motor_a"));
    else { Dict d; d["run"] = s.get_number("run_a", 0.0); motor_a.apply_state(d); }
    if (s.get("motor_b").is_dict()) motor_b.apply_state(s.get("motor_b"));
    else { Dict d; d["run"] = s.get_number("run_b", 0.0); motor_b.apply_state(d); }
    hv1001_position = s.get_number("hv1001", 0.0);
    fresh_feed = s.get_number("feed", 66.2);
    fresh_temperature = s.get_number("feed_t", 38.0);
    fresh_pressure = s.get_number("feed_p", 6.0);
    fresh_lightfrac = s.get_number("feed_z", 0.12);
}

std::vector<ParameterSpec> FeedSection::parameters() const {
    return {{"D1_AREA", D1_AREA, "m2", "D1 horizontal cross-sectional area", 0.1, 1000.0, true},
            {"D1_HEIGHT", D1_HEIGHT, "m", "D1 effective liquid height", 0.1, 50.0, true},
            {"DENSITY", DENSITY, "kg/m3", "Charge liquid reference density", 100.0, 2000.0, true},
            {"STATIC_HEAD", STATIC_HEAD, "bar", "D1 liquid static head at pump suction", 0.0, 20.0, true},
            {"VAPOUR_PRESSURE", VAPOUR_PRESSURE, "barg", "Charge vapour pressure at operating temperature", -1.0, 20.0, true}};
}

}  // namespace azeocore::units

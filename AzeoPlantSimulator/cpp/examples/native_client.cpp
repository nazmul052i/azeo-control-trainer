// A small flow-measurement rig composed from the production DLL. No Python,
// Qt, repository paths, sockets or simulator UI are required by this client.
#include <cmath>
#include <iostream>
#include <stdexcept>

#include "azeocore/devices.hpp"
#include "azeocore/iobus.hpp"
#include "azeocore/version.h"

int main() {
    try {
        if (azeocore_abi_version() != 1) throw std::runtime_error("SDK/DLL ABI mismatch");
        azeocore::TagDatabase db;
        db.analog("FCV-1", azeocore::TagKind::AO, "RIG", "Valve demand", "%", 0, 100, 0);
        auto& flow = db.analog("FT-1", azeocore::TagKind::AI, "RIG", "Measured flow", "m3/h", 0, 100, 0);
        azeocore::IOBus io(db);
        if (!io.register_dcs("example-controller")) throw std::runtime_error("Output holder unavailable");
        azeocore::Lag process(2.0, 0.0);
        const azeocore::PendingValue demand{true, 60.0, true};
        if (io.write_from_dcs("FCV-1", demand, "example-controller") != azeocore::WriteVerdict::Applied)
            throw std::runtime_error("Output write rejected");
        if (io.write_from_dcs("FT-1", demand, "example-controller") != azeocore::WriteVerdict::RejectedOwnership)
            throw std::runtime_error("Input ownership contract failed");
        for (int step = 0; step < 100; ++step) {
            std::lock_guard<azeocore::RecursiveLock> guard(db.lock);
            flow.set(process.step(db.at("FCV-1").effective(), 0.1));
            io.tick();
        }
        const auto measured = io.sample("FT-1");
        if (measured.quality != azeocore::Q_GOOD || !std::isfinite(measured.value)
                || measured.value < 59.0 || measured.value > 60.0)
            throw std::runtime_error("Process response was not a Good value near 60");
        std::cout << "Azeo Core " << azeocore_version() << ": FT-1=" << measured.value
                  << " m3/h; quality=Good; input writes refused\n";
        io.release_dcs("example-controller");
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

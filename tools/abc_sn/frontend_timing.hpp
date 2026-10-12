// Used only by the local copy of upstream snMain.cpp. Importer stays unchanged.
#include <chrono>
#include <fstream>
static const auto lhdtrack_frontend_start = std::chrono::steady_clock::now();
static void lhdtrack_frontend_timing(const char* output, const sn_slang_timing_t& timing) {
    const double total = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - lhdtrack_frontend_start).count();
    std::ofstream file(std::string(output) + ".timing.json");
    file << "{\"schema_version\":1,\"status\":\"ok\",\"timing_ms\":{"
         << "\"slang_parse\":" << timing.parse_seconds * 1000
         << ",\"slang_elaborate\":" << timing.elaborate_seconds * 1000
         << ",\"sn_import\":" << timing.import_seconds * 1000
         << ",\"total\":" << total << "}}\n";
}

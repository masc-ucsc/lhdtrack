// Run SN through LiveHD's standalone ABC flow executor (linked at build time).
#include <algorithm>
#include <cmath>
#include <chrono>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <string>
#include <vector>

#include "abc_flow.hpp"
#include "liberty_dff.hpp"
#include "recipe.hpp"
extern "C" {
#include "base/abc/abc.h"
#include "base/main/main.h"
#include "base/cmd/cmd.h"
#include "map/scl/sclLib.h"
#include "map/scl/sclSize.h"
#include "misc/extra/extra.h"
}
#include "base/sn/sn.h"
#include "base/sn/snCheck.h"

using Clock = std::chrono::steady_clock;
static double milliseconds(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}
struct Command_time { std::string stage, command; double ms; };
static std::vector<Command_time> command_times;
static std::string profile_stage = "initialization";
static unsigned command_depth = 0;
// Link-time interception preserves the unchanged shared executor and scripts.
// Only outer calls are counted: ABC can recursively execute script commands.
extern "C" int __real_Cmd_CommandExecute(Abc_Frame_t*, const char*);
extern "C" int __wrap_Cmd_CommandExecute(Abc_Frame_t* frame, const char* command) {
    const auto start = Clock::now();
    const bool outer = command_depth++ == 0;
    const int rc = __real_Cmd_CommandExecute(frame, command);
    --command_depth;
    if (outer) command_times.push_back({profile_stage, command, milliseconds(start)});
    return rc;
}

static bool sn_has_state(const char* path) {
    FILE* input = fopen(path, "rb");
    if (!input) return false;
    auto* design = sn_design_read_binary_checked(input, stderr);
    fclose(input);
    if (!design) return false;
    bool state = false;
    for (size_t i = 0; i < design->modules.size; ++i) {
        auto* module = sn_design_get_module_const(design, static_cast<sn_module_id_t>(i));
        state |= module->type_objects[SN_REG_OUT].size > 0;
    }
    sn_design_destroy(design);
    return state;
}

static std::string json_string(const std::string& s) {
    std::string out = "\"";
    const char* hex = "0123456789abcdef";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if (c < 32) {
            out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15];
        } else out += c;
    }
    return out + '"';
}

static std::string substitute(std::string flow, const std::string& delay,
                              const std::string& budget) {
    for (const auto& [key, value] : std::vector<std::pair<std::string, std::string>>{
             {"{D}", "-D " + delay}, {"{B}", "-D " + budget},
             {"{F}", std::to_string(kMaxFanout)}, {"{L}", ""}}) {
        size_t at;
        while ((at = flow.find(key)) != std::string::npos) flow.replace(at, key.size(), value);
    }
    return flow;
}

// Same default driving-cell selection as Abc_backend::resolve_boundary_defaults.
static void driving_cell() {
    auto* lib = static_cast<SC_Lib*>(Abc_FrameReadLibScl());
    double sum = 0; unsigned count = 0;
    SC_Cell* c; int i;
    SC_LibForEachCellClass(lib, c, i) {
        auto* rep = c->pRepr ? c->pRepr : c;
        if (rep->n_inputs < 1 || rep->n_inputs > 4 || rep->n_outputs != 1) continue;
        sum += SC_CellPinCapAve(rep); ++count;
    }
    const float load = count ? 4 * sum / count : 0;
    float fastest = std::numeric_limits<float>::infinity();
    std::vector<std::pair<SC_Cell*, float>> choices;
    SC_LibForEachCell(lib, c, i) {
        if (c->n_inputs != 1 || c->n_outputs != 1 ||
            Vec_WrdEntry(&SC_CellPin(c, 1)->vFunc, 0) != ABC_CONST(0xAAAAAAAAAAAAAAAA) ||
            !Scl_CellPinTime(c, 0)) continue;
        const float delay = Scl_LibPinArrivalEstimate(c, 0, 0, load);
        choices.emplace_back(c, delay); fastest = std::min(fastest, delay);
    }
    SC_Cell* best = nullptr;
    for (auto [cell, delay] : choices)
        if (delay <= 2 * fastest && (!best || cell->area < best->area)) best = cell;
    if (!best) best = Abc_SclFindInvertor(lib, 0);
    if (best && kMaxFanout) Abc_FrameSetDrivingCell(Extra_UtilStrsav(best->pName));
}

int main(int argc, char** argv) {
    if (argc != 6) {
        std::cerr << "usage: abc_sn_mapper input.sn liberty delay_ps mapped.v result.json\n";
        return 2;
    }
    const auto started = Clock::now();
    std::map<std::string, double> timings;
    auto stage_start = Clock::now();
    Abc_Start();
    timings["initialization"] = milliseconds(stage_start);
    auto* frame = Abc_FrameGetGlobalFrame();
    std::string status = "ok", reason;
    std::vector<std::string> commands;
    livehd::abc::Flow_plan plan;
    livehd::abc::Flow_result result;
    double margin = 0;
    auto execute = [&](const std::string& command, const std::string& on_error = "failed") {
        profile_stage = command.substr(0, command.find(' '));
        commands.push_back(command);
        const auto start = Clock::now();
        const int rc = Cmd_CommandExecute(frame, command.c_str());
        timings[profile_stage] += milliseconds(start);
        if (rc == 0) return true;
        status = on_error; reason = "ABC rejected " + command; return false;
    };
    // Inputs are local generated names. Liberty paths containing ABC command
    // metacharacters are rejected by the Python flow before launching this tool.
    do {
        if (!execute("@read " + std::string(argv[1]), "unsupported")) break;
        if (!execute("@collapse", "unsupported")) break;
        if (!execute("@read_lib " + std::string(argv[2]), "unsupported")) break;
        if (!execute("@lower_mem -S " + std::to_string(kMemoryMaxBits), "unsupported")) break;
        if (!execute("@map_ff", "unsupported")) break;
        if (!execute("@blast -c -f", "unsupported")) break;
        if (!execute("&put")) break;
        auto* ntk = Abc_FrameReadNtk(frame);
        if (!ntk) { status = "failed"; reason = "SN produced no combinational network"; break; }
        const bool timing = livehd::abc::frame_has_nldm_timing();
        stage_start = Clock::now();
        if (timing) driving_cell();
        const auto dff = livehd::liberty::find_dff_cell(argv[2]);
        // @blast -c -f exposes sequential cells as cloud endpoints. A purely
        // combinational top has no such extra endpoints beyond its RTL ports;
        // the selected state is counted by the SN survey supplied below.
        const bool has_state = sn_has_state(argv[1]);
        if (has_state && dff) margin = dff->clk_to_q_ps + dff->setup_ps;
        const float delay = std::stof(argv[3]);
        plan.budget = std::max(1.0f, delay - static_cast<float>(margin));
        const std::string budget = std::to_string(plan.budget);
        const std::string tail = timing && kMaxFanout ? std::string(kBufferTail) : "";
        plan.flow = substitute(std::string(kCombFlow) + tail, argv[3], budget);
        plan.map_step = substitute(kMapCmd, argv[3], budget);
        plan.remap_post = substitute(std::string("; ") + kPutCmd + tail, argv[3], budget);
        plan.size_to_budget = "upsize -D " + budget + "; dnsize -D " + budget;
        plan.area_flow = substitute(std::string(kAreaFlow) +
                                   (timing && kMaxFanout ? kAreaTail : ""), argv[3], budget);
        plan.ladder = timing;
        plan.remappable = true;
        plan.area_candidate = timing;
        plan.area_relax_pct = kAreaRelax;
        timings["policy_setup"] = milliseconds(stage_start);
        profile_stage = "shared_flow";
        stage_start = Clock::now();
        result = livehd::abc::execute_flow(frame, plan);
        timings["shared_flow"] = milliseconds(stage_start);
        if (result.status != livehd::abc::Flow_status::completed) {
            status = "failed"; reason = result.stage + ": " + result.command; break;
        }
        if (!execute("@put -n", "unsupported")) break;
        if (!execute("@check")) break;
        execute("@write -n " + std::string(argv[4]));
    } while (false);
    stage_start = Clock::now();
    profile_stage = "shutdown";
    Abc_Stop();
    timings["shutdown"] = milliseconds(stage_start);
    timings["total"] = milliseconds(started);
    std::ofstream out(argv[5]);
    out << "{\"status\":" << json_string(status) << ",\"reason\":" << json_string(reason)
        << ",\"flow\":" << json_string(plan.flow)
        << ",\"area_flow\":" << json_string(plan.area_flow)
        << ",\"candidate\":" << json_string(result.candidate)
        << ",\"reg_margin_ps\":" << margin << ",\"budget_ps\":" << plan.budget
        << ",\"max_fanout\":" << kMaxFanout << ",\"area_relax\":" << kAreaRelax
        << ",\"memory_max_bits\":" << kMemoryMaxBits << ",\"commands\":[";
    for (size_t i = 0; i < commands.size(); ++i) out << (i ? "," : "") << json_string(commands[i]);
    out << "],\"timing_ms\":{";
    size_t index = 0;
    for (const auto& [name, ms] : timings)
        out << (index++ ? "," : "") << json_string(name) << ":" << ms;
    out << "},\"command_timing\":[";
    for (size_t i = 0; i < command_times.size(); ++i) {
        const auto& entry = command_times[i];
        out << (i ? "," : "") << "{\"stage\":" << json_string(entry.stage)
            << ",\"command\":" << json_string(entry.command) << ",\"ms\":" << entry.ms << "}";
    }
    out << "]}\n";
    out.close();
    return status == "ok" ? 0 : 1;
}

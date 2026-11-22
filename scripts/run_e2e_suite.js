#!/usr/bin/env node
/**
 * Master E2E Test Suite Runner for FilmZilla / FilmSub
 * Executes Phase 0 (regression suites) and Phases 1-4 (Tier 1-4 E2E suites).
 * Supports CLI flags: --tier=1, --tier=2, --tier=3, --tier=4, --regression
 */

const path = require('path');
const { spawnSync } = require('child_process');

const ROOT_DIR = path.resolve(__dirname, '..');

// Parse CLI flags
const args = process.argv.slice(2);
const tierArg = args.find(a => a.startsWith('--tier='));
const selectedTier = tierArg ? tierArg.split('=')[1].trim() : null;
const hasRegressionFlag = args.includes('--regression');

const colors = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  cyan: '\x1b[36m',
  gray: '\x1b[90m'
};

// Validate --tier argument: must be 1, 2, 3, or 4
const VALID_TIERS = ['1', '2', '3', '4'];
if (selectedTier !== null && !VALID_TIERS.includes(selectedTier)) {
  console.error(`${colors.red}${colors.bold}Error: Invalid tier "${selectedTier}". Valid tiers are: 1, 2, 3, 4.${colors.reset}`);
  process.exit(1);
}

const suiteResults = [];

function runChildScript(label, scriptPath, targetCount) {
  console.log(`\n${colors.cyan}▶ RUNNING: ${label}${colors.reset} (${scriptPath})`);
  const fullPath = path.isAbsolute(scriptPath) ? scriptPath : path.join(ROOT_DIR, scriptPath);

  const start = Date.now();
  const proc = spawnSync(process.execPath, [fullPath], {
    cwd: ROOT_DIR,
    encoding: 'utf8',
    env: { ...process.env, FORCE_COLOR: '1' }
  });
  const duration = ((Date.now() - start) / 1000).toFixed(2);

  const stdout = proc.stdout || '';
  const stderr = proc.stderr || '';
  const isPass = proc.status === 0;

  // Print output
  if (stdout.trim()) {
    console.log(stdout.trim());
  }
  if (stderr.trim()) {
    console.error(`${colors.yellow}${stderr.trim()}${colors.reset}`);
  }

  if (!isPass) {
    console.error(`${colors.red}❌ FAILED: ${label} (exit code ${proc.status})${colors.reset}`);
    if (proc.error && proc.error.stack) {
      console.error(`${colors.yellow}Process Error Stack: ${proc.error.stack}${colors.reset}`);
    }
  }

  // Parse pass/fail counts if present
  let passCount = 0;
  let failCount = 0;
  const summaryMatch = stdout.match(/SUMMARY:\s*(\d+)\s*passed,\s*(\d+)\s*failed/i);
  if (summaryMatch) {
    passCount = parseInt(summaryMatch[1], 10);
    failCount = parseInt(summaryMatch[2], 10);
  } else if (isPass) {
    passCount = targetCount;
  } else {
    failCount = 1;
  }

  const resultItem = {
    label,
    scriptPath,
    isPass,
    exitCode: proc.status,
    passCount,
    failCount,
    targetCount,
    duration: `${duration}s`
  };
  suiteResults.push(resultItem);
  return resultItem;
}

console.log(`${colors.bold}========================================================================${colors.reset}`);
console.log(`${colors.bold}🎬 FILMZILLA / FILMSUB MASTER E2E TEST RUNNER${colors.reset}`);
console.log(`${colors.gray}Working directory: ${ROOT_DIR}${colors.reset}`);
if (selectedTier && hasRegressionFlag) {
  console.log(`${colors.yellow}Filter: Running Phase 0 Regression Suites AND Tier ${selectedTier}${colors.reset}`);
} else if (selectedTier) {
  console.log(`${colors.yellow}Filter: Running only Tier ${selectedTier}${colors.reset}`);
} else if (hasRegressionFlag) {
  console.log(`${colors.yellow}Filter: Running only Phase 0 Regression Suites${colors.reset}`);
} else {
  console.log(`${colors.green}Mode: Full Regression & Tier 1-4 Verification${colors.reset}`);
}
console.log(`${colors.bold}========================================================================${colors.reset}`);

// Execution filters:
// - Phase 0 runs if no tier filter is specified OR if --regression is present
const runPhase0 = !selectedTier || hasRegressionFlag;
// - Tier suites run if matching --tier=X OR if full run (!selectedTier && !hasRegressionFlag)
const runTier1 = selectedTier === '1' || (!selectedTier && !hasRegressionFlag);
const runTier2 = selectedTier === '2' || (!selectedTier && !hasRegressionFlag);
const runTier3 = selectedTier === '3' || (!selectedTier && !hasRegressionFlag);
const runTier4 = selectedTier === '4' || (!selectedTier && !hasRegressionFlag);

// PHASE 0: Existing Regression Suites
if (runPhase0) {
  console.log(`\n${colors.bold}${colors.yellow}--- PHASE 0: EXISTING REGRESSION SUITES ---${colors.reset}`);
  runChildScript('Phase 0.1: UI Refinements', 'scratch/test_ui_refinements.js', 9);
  runChildScript('Phase 0.2: Couple Cam & Voice', 'scratch/test_couple_cam_voice.js', 4);
  runChildScript('Phase 0.3: Teasers Player & YouTube', 'scratch/test_teasers_player.js', 6);
  runChildScript('Phase 0.4: Teaser Reels E2E Feed', 'scratch/test_teaser_reels_e2e.js', 16);
  runChildScript('Phase 0.5: Poll Creator Betting Unit', 'scratch/test_poll_creator.js', 5);
  runChildScript('Phase 0.6: VIP Cinema 7 Features Suite', 'scripts/test_vip_cinema_suite.js', 16);
}

// PHASE 1: Tier 1 Feature Coverage (45 tests)
if (runTier1) {
  console.log(`\n${colors.bold}${colors.yellow}--- PHASE 1: TIER 1 FEATURE COVERAGE (45 TESTS) ---${colors.reset}`);
  runChildScript('Phase 1: Tier 1 Features', 'scratch/test_e2e_tier1_features.js', 45);
}

// PHASE 2: Tier 2 Boundary & Corner Cases (45 tests)
if (runTier2) {
  console.log(`\n${colors.bold}${colors.yellow}--- PHASE 2: TIER 2 BOUNDARIES & CORNERS (45 TESTS) ---${colors.reset}`);
  runChildScript('Phase 2: Tier 2 Boundaries', 'scratch/test_e2e_tier2_boundaries.js', 45);
}

// PHASE 3: Tier 3 Cross-Feature Combinations (10 tests)
if (runTier3) {
  console.log(`\n${colors.bold}${colors.yellow}--- PHASE 3: TIER 3 CROSS-FEATURE INTERACTIONS (10 TESTS) ---${colors.reset}`);
  runChildScript('Phase 3: Tier 3 Pairwise', 'scratch/test_e2e_tier3_pairwise.js', 10);
}

// PHASE 4: Tier 4 Real-World Application Scenarios (5 tests)
if (runTier4) {
  console.log(`\n${colors.bold}${colors.yellow}--- PHASE 4: TIER 4 REAL-WORLD SCENARIOS (5 WORKFLOWS) ---${colors.reset}`);
  runChildScript('Phase 4: Tier 4 Scenarios', 'scratch/test_e2e_tier4_scenarios.js', 5);
}

// =========================================================================
// ANSI SUMMARY TABLE
// =========================================================================
console.log(`\n${colors.bold}========================================================================${colors.reset}`);
console.log(`${colors.bold}📊 MASTER E2E TEST EXECUTION SUMMARY${colors.reset}`);
console.log(`${colors.bold}========================================================================${colors.reset}`);

const pad = (str, len) => String(str).padEnd(len, ' ');
const padR = (str, len) => String(str).padStart(len, ' ');

console.log(`┌──────────────────────────────────────────────┬──────────────┬──────────┬───────────┐`);
console.log(`│ ${pad('Test Suite / Phase', 44)} │ ${pad('Result', 12)} │ ${padR('Passed', 8)} │ ${padR('Duration', 9)} │`);
console.log(`├──────────────────────────────────────────────┼──────────────┼──────────┼───────────┤`);

let totalPassed = 0;
let totalFailed = 0;
let suitesPassed = 0;
let suitesFailed = 0;

for (const res of suiteResults) {
  totalPassed += res.passCount;
  totalFailed += res.failCount;
  if (res.isPass) suitesPassed++;
  else suitesFailed++;

  const statusStr = res.isPass
    ? `${colors.green}PASS ✅${colors.reset}    `
    : `${colors.red}FAIL ❌${colors.reset}    `;
  const countStr = `${res.passCount}/${res.targetCount}`;

  console.log(`│ ${pad(res.label, 44)} │ ${statusStr} │ ${padR(countStr, 8)} │ ${padR(res.duration, 9)} │`);
}

console.log(`└──────────────────────────────────────────────┴──────────────┴──────────┴───────────┘`);

console.log(`\n${colors.bold}Metrics Summary:${colors.reset}`);
console.log(`- Total Test Checks Passed: ${colors.green}${totalPassed}${colors.reset}`);
console.log(`- Total Test Checks Failed: ${totalFailed > 0 ? colors.red : colors.green}${totalFailed}${colors.reset}`);
console.log(`- Test Suites Passed:       ${colors.green}${suitesPassed}${colors.reset} / ${suiteResults.length}`);
console.log(`- Test Suites Failed:       ${suitesFailed > 0 ? colors.red : colors.green}${suitesFailed}${colors.reset} / ${suiteResults.length}`);

if (suitesFailed > 0) {
  console.log(`\n${colors.yellow}Notice: Failing suites in Phase 0-4 pinpoint pending milestone implementations:${colors.reset}`);
  console.log(`  1. movie.html missing restored controls (btn-toggle-chat-popups, btn-bar-mic, btn-bar-cam) [M4]`);
  console.log(`  2. movie.html duplicate IDs (movie-fav-btn, movie-later-btn) [M4]`);
  console.log(`  3. index.html hero watermark bubble pod cleanup [M3]`);
  console.log(`  4. style.css 7.5px font-size elimination & neon drop shadows [M1/M3]`);
  console.log(`  5. app.js location.hash #vip-hub modal deep-link [M2]`);
}

console.log(`\n${colors.bold}========================================================================${colors.reset}`);

// Exit code discipline: exit with code 1 if any suite failed, 0 if all passed
process.exit(suitesFailed > 0 ? 1 : 0);

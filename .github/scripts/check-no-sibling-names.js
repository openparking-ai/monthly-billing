#!/usr/bin/env node
/**
 * No name from the maintainer's other, private software may appear in this
 * repository. Not in code, a comment, a document, a test, a fixture, a commit
 * message or a pull request body.
 *
 * Usage:
 *   check-no-sibling-names.js              scan every tracked file, and every
 *                                          tracked file's PATH
 *   check-no-sibling-names.js --self-test  prove the scan can fail
 *   check-no-sibling-names.js --worktree   scan the working tree too: tracked
 *                                          files, files git is not tracking
 *                                          yet, and a built dist/ if there is
 *                                          one -- contents and paths -- with a
 *                                          control planted and caught in the
 *                                          same run first
 *
 * PATHS ARE TEXT TOO. A file's name is in the repository as surely as its
 * contents are, and a guard that read contents only let a private product's
 * name sit in a tracked path of this repository unnoticed. So every path is
 * tokenised and digested exactly as a line is.
 *
 * ---------------------------------------------------------------------------
 * WHY THE FORBIDDEN WORDS ARE NOT IN THIS REPOSITORY AT ALL
 * ---------------------------------------------------------------------------
 * A guard that spells out the words it forbids breaks the rule it enforces. It
 * used to hold them as unsalted sha256 of the lowercased word, each labelled
 * with its kind -- and a short lowercase name is found from its unsalted digest
 * by trying candidates, so a public list of them was a list of the names with
 * one step in the way, and the labels said which were hostnames. The names
 * that list protected are to be treated as disclosed.
 *
 * So the list is a CI SECRET, not a file: `SIBLING_NAMES_DIGESTS` holds an
 * HMAC-SHA256 of each lowercased word under the key in `SIBLING_NAMES_HMAC_KEY`,
 * hex, separated by whitespace or commas, and `SIBLING_NAMES_COUNT` holds how
 * many there are. No kind travels with them. Without the key a digest cannot be
 * tested against a guess, and the key is in no file. A scan with no list FAILS
 * -- it does not pass for want of words, which is the evidence-that-cannot-be-
 * negative failure this project catalogues. The report prints a prefix of the
 * keyed digest and never the word.
 *
 * WHAT THE SELF-TEST PROVES, AND WHAT IT DOES NOT. It proves the MECHANISM,
 * under a key it makes for itself: that a token whose keyed digest is on the
 * list is found, in a real file, and rejected, and that the same word under
 * another key is not. It cannot prove that the secret holds the right words --
 * nothing inside this repository can, because that would require the words.
 * They are derived from the private source, which records their count.
 *
 * ---------------------------------------------------------------------------
 * WHY TOKENS, NOT A REGULAR EXPRESSION
 * ---------------------------------------------------------------------------
 * A substring search is wrong in both directions and both were measured on the
 * sibling repositories before this was written:
 *
 *   - FALSE POSITIVES. One forbidden word is a substring of an ordinary English
 *     word this project uses constantly. A plain search fires on four files
 *     across two sibling repositories, every hit innocent.
 *   - AND A WORD-BOUNDARY REGEX IS NOT THE FIX. BSD `grep -E` -- which is what
 *     `grep` is on the maintainer's machine -- silently matches NOTHING when
 *     three or more `\b`-anchored alternatives are combined. An absence check
 *     built on it reports clean because it cannot match, which is this
 *     project's one disease: evidence that cannot produce a negative result.
 *
 * So the text is TOKENISED instead, and each token is digested whole. Runs of
 * letters are the tokens; digits, punctuation and case transitions all end one,
 * so `oneWordTwo`, `one_word_two` and `one-word-2` all yield the same three.
 * There is no regular expression engine in the decision and nothing to get
 * wrong per-platform.
 */

import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, unlinkSync, existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { createHmac, randomBytes } from 'node:crypto';

/**
 * The keyed digests of the forbidden words, from the environment CI fills from
 * its secrets -- see the header. Read when a real scan needs them, never by
 * the self-test, which makes its own key. Fails closed: no key, no list, a
 * malformed entry or a count that does not match is exit 1, never "clean".
 */
function forbiddenFromSecrets() {
  const key = process.env.SIBLING_NAMES_HMAC_KEY ?? '';
  const listed = (process.env.SIBLING_NAMES_DIGESTS ?? '').split(/[\s,]+/).filter(Boolean);
  const declared = Number.parseInt(process.env.SIBLING_NAMES_COUNT ?? '', 10);
  const problems = [];
  if (!key) problems.push('SIBLING_NAMES_HMAC_KEY is not set');
  if (!listed.length) problems.push('SIBLING_NAMES_DIGESTS is not set or empty');
  if (listed.some((d) => !/^[0-9a-f]{64}$/.test(d))) {
    problems.push('SIBLING_NAMES_DIGESTS holds an entry that is not 64 lowercase hex digits');
  }
  const digests = new Set(listed);
  if (!Number.isInteger(declared) || declared !== digests.size) {
    problems.push(
      `SIBLING_NAMES_COUNT is ${process.env.SIBLING_NAMES_COUNT ?? 'unset'} and the list ` +
        `holds ${digests.size} distinct digests`,
    );
  }
  if (problems.length) {
    console.error('The forbidden-name list could not be read, so nothing was scanned:');
    for (const line of problems) console.error(`  ${line}`);
    console.error(
      'The list is a CI secret, generated from the private source. A scan without it ' +
        'is not a clean scan.',
    );
    process.exit(1);
  }
  return { key, digests };
}

/** The one sanctioned mention of the maintainer, which is an instruction. */
const ATTRIBUTION = 'Built by 72 Knots Method by 72Knots.ai';

//: This file holds digests, never words, so it scans itself like any other.
//: LICENSE and a lockfile stay out: neither is ours to edit.
const SKIP = /^(LICENSE|package-lock\.json)$/;

const digestOf = (key, value) =>
  createHmac('sha256', key).update(value.trim().toLowerCase()).digest('hex');

/**
 * Every run of letters in `text`, lowercased, with a case transition ending a
 * token as surely as a space does.
 */
export function tokens(text) {
  const out = [];
  let current = '';
  let previousWasLower = false;

  for (const ch of text) {
    const isLetter = /\p{L}/u.test(ch);
    if (!isLetter) {
      if (current) out.push(current.toLowerCase());
      current = '';
      previousWasLower = false;
      continue;
    }
    const isUpper = ch === ch.toUpperCase() && ch !== ch.toLowerCase();
    if (isUpper && previousWasLower && current) {
      out.push(current.toLowerCase());
      current = '';
    }
    current += ch;
    previousWasLower = !isUpper;
  }
  if (current) out.push(current.toLowerCase());
  return out;
}

function trackedFiles() {
  return execFileSync('git', ['ls-files'], { encoding: 'utf8' })
    .split('\n')
    .filter(Boolean)
    .filter((f) => !SKIP.test(f));
}

/** Every commit message in `range`, so a name cannot arrive through git metadata. */
function commitMessages(range) {
  const args = ['log', '--format=%H%x1f%B%x1e'];
  if (range) args.push(range);

  let out;
  try {
    out = execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (error) {
    //: An unborn branch has no messages to scan, and that is the only failure
    //: swallowed here. Everything else is rethrown, LOUDLY: a `git log` that
    //: fails for any other reason and is treated as "no commits" is a check
    //: that cannot produce a negative result, which is the thing this whole
    //: project exists to keep out.
    const stderr = String(error.stderr ?? '');
    if (stderr.includes('does not have any commits yet')) return [];
    throw error;
  }

  return out
    .split('\x1e')
    .map((chunk) => chunk.trim())
    .filter(Boolean)
    .map((chunk) => {
      const [sha, body] = chunk.split('\x1f');
      return { where: `commit ${sha.slice(0, 12)}`, text: body ?? '' };
    });
}

/**
 * Report every forbidden token in `text`, by keyed digest. Never prints the
 * token. `forbidden` is `{ key, digests }`.
 */
function scan(where, text, forbidden) {
  const failures = [];
  const seen = new Set();
  for (const token of tokens(text)) {
    const digest = digestOf(forbidden.key, token);
    if (!forbidden.digests.has(digest) || seen.has(digest)) continue;
    seen.add(digest);
    failures.push(`${where}: a forbidden name (hmac ${digest.slice(0, 12)}...)`);
  }
  return failures;
}

/** Report every forbidden token in each PATH, by keyed digest. */
function scanPaths(files, forbidden) {
  return files.flatMap((file) => scan(`path ${file}`, file, forbidden));
}

function selfTest() {
  //: A word this repository will never contain, so the control cannot pass by
  //: accident, and no forbidden word is needed to prove the mechanism fires.
  const control = 'zzqxcontrolword';
  const key = randomBytes(32).toString('hex');
  const digests = { key, digests: new Set([digestOf(key, control)]) };
  //: Keyed: the same word's digest under another key is not on the list.
  const otherKey = { key: randomBytes(32).toString('hex'), digests: digests.digests };
  const path = `.self-test-${process.pid}.tmp`;

  let planted;
  let clean;
  let camel;
  let innocent;
  try {
    writeFileSync(path, `a line that mentions ${control} once\n`);
    planted = scan(path, readFileSync(path, 'utf8'), digests);

    //: The case-transition split is load-bearing: a name hidden inside an
    //: identifier must be found, or the guard is one rename away from blind.
    writeFileSync(path, `const someName = { prefix${control[0].toUpperCase()}${control.slice(1)}Suffix: 1 };\n`);
    camel = scan(path, readFileSync(path, 'utf8'), digests);

    writeFileSync(path, 'a line that mentions nothing of the sort\n');
    clean = scan(path, readFileSync(path, 'utf8'), digests);

    //: And the negative control for the whole tokenising design: a longer word
    //: that CONTAINS the forbidden one must pass. A substring search fails here.
    writeFileSync(path, `a line containing xx${control}yy as one word\n`);
    innocent = scan(path, readFileSync(path, 'utf8'), digests);
  } finally {
    try { unlinkSync(path); } catch { /* already gone */ }
  }

  //: The path scan, on the same terms: a directory or file NAMED with the word
  //: is found; a path holding a longer word that contains it is not.
  const namedPath = scanPaths([`test/fixtures/${control}/a.txt`, `docs/x-${control}.md`], digests);
  const innocentPath = scanPaths([`test/fixtures/xx${control}yy/a.txt`], digests);
  const underAnotherKey = scan('another key', `a line that mentions ${control} once`, otherKey);

  const ok =
    planted.length === 1 && camel.length === 1 && clean.length === 0 && innocent.length === 0 &&
    namedPath.length === 2 && innocentPath.length === 0 && underAnotherKey.length === 0;
  if (!ok) {
    console.error('self-test FAILED — this guard cannot be trusted.');
    console.error(`  planted:  ${planted.length} (want 1)`);
    console.error(`  camel:    ${camel.length} (want 1)`);
    console.error(`  clean:    ${clean.length} (want 0)`);
    console.error(`  innocent: ${innocent.length} (want 0)`);
    console.error(`  path:     ${namedPath.length} (want 2)`);
    console.error(`  innocent path: ${innocentPath.length} (want 0)`);
    console.error(`  another key: ${underAnotherKey.length} (want 0)`);
    return false;
  }
  console.log('self-test OK — a planted name fails, inside an identifier too, and in a path;');
  console.log('               a clean line passes, and so does a longer word containing it,');
  console.log('               and the same word under another key does not match.');
  return true;
}

/** Files git is not tracking and not ignoring: what `git add -A` would add. */
function untrackedFiles() {
  return execFileSync('git', ['ls-files', '--others', '--exclude-standard'], { encoding: 'utf8' })
    .split('\n')
    .filter(Boolean)
    .filter((f) => !SKIP.test(f));
}

/** A built site, if this repository has one. Ignored by git, and exactly what would be served. */
function builtFiles(dir = 'dist') {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? builtFiles(join(dir, e.name)) : [join(dir, e.name)],
  );
}

function scanFiles(files, digests) {
  const failures = [];
  for (const file of files) {
    let text;
    try {
      text = readFileSync(file, 'utf8');
    } catch {
      continue;
    }
    failures.push(...scan(file, text, digests));
  }
  return failures;
}

/**
 * The working tree, with its positive controls in the same run: one file
 * holding the control word, one file NAMED with it, written into the tree; the
 * same walk must find each where it is and nothing else, and only then is the
 * real walk's result read.
 */
function worktree() {
  //: Assembled at runtime: this file is inside the walk, and the whole word
  //: written here would be caught here, as well as in the planted files.
  const control = ['zzqx', 'inrun', 'control'].join('');
  const planted = `.names-control-${process.pid}.tmp`;
  const named = `.names-${control}-${process.pid}.tmp`;
  const walk = () => [...new Set([...trackedFiles(), ...untrackedFiles(), ...builtFiles()])];
  let caught;
  try {
    writeFileSync(planted, `a line that mentions ${control} once\n`);
    writeFileSync(named, 'a line that mentions nothing of the sort\n');
    const key = randomBytes(32).toString('hex');
    const digests = { key, digests: new Set([digestOf(key, control)]) };
    const files = walk();
    caught = [...scanFiles(files, digests), ...scanPaths(files, digests)];
  } finally {
    for (const f of [planted, named]) {
      try { unlinkSync(f); } catch { /* already gone */ }
    }
  }
  const inContents = caught.filter((l) => l.startsWith(`${planted}:`));
  const inPath = caught.filter((l) => l.startsWith(`path ${named}:`));
  if (caught.length !== 2 || inContents.length !== 1 || inPath.length !== 1) {
    console.error('in-run control FAILED — the working-tree walk cannot be trusted.');
    for (const line of caught) console.error(`  ${line}`);
    process.exit(1);
  }
  console.log(`in-run control OK — the planted word was caught in ${planted} and in the path ${named}, and nowhere else.`);

  const forbidden = forbiddenFromSecrets();
  const tracked = trackedFiles();
  const untracked = untrackedFiles();
  const built = builtFiles();
  const files = walk();
  const failures = [...scanFiles(files, forbidden), ...scanPaths(files, forbidden)];
  for (const { where, text } of commitMessages()) failures.push(...scan(where, text, forbidden));
  if (failures.length) {
    console.error('A name from the private estate appears in the working tree:\n');
    for (const line of failures) console.error(`  ${line}`);
    process.exit(1);
  }
  console.log(
    `clean — ${tracked.length} tracked, ${untracked.length} untracked and ${built.length} built files, ` +
      'their paths, and every commit message, carry no such name.',
  );
}

function main() {
  if (process.argv[2] === '--self-test') process.exit(selfTest() ? 0 : 1);
  if (process.argv[2] === '--worktree') return worktree();

  const range = process.argv[2];
  const forbidden = forbiddenFromSecrets();
  const failures = [];

  failures.push(...scanPaths(trackedFiles(), forbidden));

  for (const file of trackedFiles()) {
    let text;
    try {
      text = readFileSync(file, 'utf8');
    } catch {
      continue; // not text; nothing to read a name out of
    }
    failures.push(...scan(file, text, forbidden));
  }

  for (const { where, text } of commitMessages(range)) {
    failures.push(...scan(where, text, forbidden));
  }

  //: The attribution is an instruction and is checked for PRESENCE, not
  //: absence -- the one sanctioned mention, spelled exactly, no period between
  //: the two halves. It is asserted here rather than in a document so that
  //: deleting it from README.md turns a check red instead of going unnoticed.
  const readme = readFileSync('README.md', 'utf8');
  if (!readme.includes(ATTRIBUTION)) {
    failures.push(`README.md: the attribution line is missing or reworded: "${ATTRIBUTION}"`);
  }

  if (failures.length) {
    console.error('A name from the private estate appears in this repository:\n');
    for (const line of failures) console.error(`  ${line}`);
    console.error('\nNothing outside this project may be named here. See CONTRIBUTING.md.');
    process.exit(1);
  }
  console.log(`clean — ${trackedFiles().length} tracked files, their paths and the commit range carry no such name.`);
}

main();

export const meta = {
  name: 'kb-pipeline',
  description: 'klode-kit two-stage pipeline shell. Stage 1 (default) runs preflight, ocr, audit and ends at awaiting_confirm (exit 10): open the confirm page, decide, export choices.json. Stage 2 (args.resume) runs writeback, build, check; publish runs only with args.publish === true.',
}

// WI-14. A thin shell (design r2.5, H1): the script only sequences stages. Each stage is ONE agent() call that runs ONE
// klode-kit command and hands back its exit code and output; every decision lives in `klode-kit pipeline`, whose
// stages are idempotent (done stages with equal hashes are reused), so a relaunch repeats the same agent() calls.
// The runtime offers no file, process, timestamp or random API to this script (docs: workflows, "Behavior and
// limits"), and none is used: anything that needs the disk or the clock is the agent's command.
//
// args (an object):
//   project   required  absolute path of the data project (cwd of the pipeline)
//   inbox     required  the inbox directory (or one PDF) of this batch, as given to `klode-kit pipeline --batch`
//   routes    optional  comma-separated subset of the OCR routes
//   type      optional  'lab' or 'rx' (the document type, handed to kb-ocr)
//   resume    optional  true: run stage 2 (writeback, build, check) instead of stage 1
//   choices   resume    absolute path of the choices.json the confirm page exported
//   no_choices resume   true instead of `choices` when audit found nothing to confirm
//   publish   optional  true: run the publish stage after check. Publishing writes .mcp.json, the registry, agents
//                       and settings, so it is a manual gate (deviations WI-09d / WI-13a): never implied by resume.

const LAUNCHER = '${CLAUDE_PLUGIN_ROOT}/bin/klode-kit'

const RESULT = {
  type: 'object',
  required: ['stage', 'exit_code', 'stdout'],
  properties: {
    stage: { type: 'string' },
    exit_code: { type: 'integer' },
    stdout: { type: 'string' },
  },
}

const FIRST_STAGES = ['preflight', 'ocr', 'audit']
const RESUME_STAGES = ['writeback', 'build', 'check']
const PUBLISH_STAGE = 'publish'

const input = args && typeof args === 'object' ? args : {}

function bad(value) {
  return typeof value !== 'string' || value === '' || /['\n\r\0]/.test(value)
}

function fail(error) {
  return { error, stage: input.resume ? 2 : 1, stages: [], exit_code: null, awaiting_confirm: false }
}

if (bad(input.project)) return fail('args.project (absolute path of the data project) is required')
if (bad(input.inbox)) return fail('args.inbox (the batch inbox directory or PDF) is required')
if (input.routes !== undefined && (bad(input.routes) || !/^[a-z,]+$/.test(input.routes))) {
  return fail('args.routes must be a comma-separated list of route names')
}
if (input.type !== undefined && input.type !== 'lab' && input.type !== 'rx') return fail("args.type must be 'lab' or 'rx'")
if (input.resume && input.no_choices !== true && bad(input.choices)) {
  return fail('args.choices (the exported choices.json) is required with args.resume; pass args.no_choices: true when the audit found nothing to confirm')
}
if (input.resume && input.choices !== undefined && bad(input.choices)) return fail('args.choices must be a path')

const stages = input.resume ? RESUME_STAGES.slice() : FIRST_STAGES.slice()
if (input.resume && input.publish === true) stages.push(PUBLISH_STAGE)

function commandFor(stage) {
  let c = LAUNCHER + ' pipeline --batch \'' + input.inbox + '\' --project \'' + input.project + '\' --stage ' + stage
  if (input.routes) c += ' --routes ' + input.routes
  if (input.type) c += ' --type ' + input.type
  if (input.resume && input.choices) c += ' --choices \'' + input.choices + '\''
  return c
}

const results = []
for (const stage of stages) {
  const r = await agent(
    'Run exactly this one shell command with the Bash tool, once, and do nothing else. Do not retry it, do not run any ' +
      'other command, do not edit any file:\n\n' + commandFor(stage) + '\n\n' +
      'If the shell has no CLAUDE_PLUGIN_ROOT, run the same command with `klode-kit` in place of ' +
      '`${CLAUDE_PLUGIN_ROOT}/bin/klode-kit` (the plugin bin/ folder is on PATH). ' +
      'Return the stage name "' + stage + '", the exit code of the command, and its complete standard output verbatim.',
    { label: stage, schema: RESULT, effort: 'low' },
  )
  results.push(r)
  const awaiting = stage === 'audit' && !input.resume && r && r.exit_code === 10
  if (!r || (r.exit_code !== 0 && !awaiting)) break
}

const last = results[results.length - 1]
const done = results.length === stages.length && !!last && (last.exit_code === 0 || last.exit_code === 10)
const awaitingConfirm = !input.resume && done && last.stage === 'audit' && last.exit_code === 10

let batchId = null
if (last && typeof last.stdout === 'string') {
  for (const line of last.stdout.split('\n')) {
    if (line.trim().startsWith('{')) {
      try {
        const parsed = JSON.parse(line)
        if (parsed && typeof parsed.batch_id === 'string') batchId = parsed.batch_id
      } catch (e) {
        // not the summary line
      }
    }
  }
}

let next = null
if (awaitingConfirm) {
  next = 'Awaiting your decisions. Open confirm/<alias>/confirm.html (the date alias of work/' + (batchId || '<batch>') +
    ', made by /klode-kit:med-intake) or the newest work/' + (batchId || '<batch>') + '/audit/a<N>/confirm.html, choose, export ' +
    'choices.json, then run this workflow again with args {resume: true, choices: <path to choices.json>}.'
} else if (input.resume && done && input.publish !== true) {
  next = 'Built and checked, not published: publishing writes .mcp.json, the registry, agents and settings. Run it ' +
    'yourself (kb-publish) or run this workflow again with args.publish: true.'
}

return {
  stage: input.resume ? 2 : 1,
  stages: results,
  exit_code: last ? last.exit_code : null,
  awaiting_confirm: awaitingConfirm,
  published: input.resume === true && input.publish === true && done,
  batch_id: batchId,
  next,
}

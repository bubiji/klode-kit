export const meta = {
  name: 'kb-pipeline',
  description: 'klode-kit two-stage pipeline shell (WI-00d2 spike). Stage 1 runs preflight, ocr, audit and stops at awaiting_confirm (exit 10); with args.resume it runs writeback, build, check.',
}

// Thin shell (design r2.5, H1): one agent per stage runs the stage command and returns its result verbatim.
// No timestamps or randomness in this script, so a relaunched run repeats the same agent() calls.
const RESULT = {
  type: 'object',
  required: ['stage', 'exit_code', 'stdout'],
  properties: {
    stage: { type: 'string' },
    exit_code: { type: 'integer' },
    stdout: { type: 'string' },
  },
}

const input = args && typeof args === 'object' ? args : {}
const project = input.project
const batch = input.batch || 'b1'
const stages = input.resume ? ['writeback', 'build', 'check'] : ['preflight', 'ocr', 'audit']

if (!project) {
  return { error: 'args.project (absolute path of the data project) is required', stages: [] }
}

const results = []
for (const stage of stages) {
  const command = `klode-kit dev spike-pipeline --stage ${stage} --project ${project} --batch ${batch}`
  const r = await agent(
    `Run exactly this one shell command with the Bash tool, once, and do nothing else:\n\n${command}\n\n` +
      'Return the stage name, the exit code of the command, and its complete standard output verbatim.',
    { label: stage, schema: RESULT, effort: 'low' },
  )
  results.push(r)
  if (!r || r.exit_code !== 0) break
}

const last = results[results.length - 1]
return {
  stage: input.resume ? 2 : 1,
  stages: results,
  exit_code: last ? last.exit_code : null,
  awaiting_confirm: !input.resume && !!last && last.exit_code === 10,
}

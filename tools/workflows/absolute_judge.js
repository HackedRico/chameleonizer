export const meta = {
  name: 'absolute-judge',
  description: 'In-session absolute rubric scores for one bench run, a stand-in for tools.judge --run',
  whenToUse: 'When the Anthropic API judge is unavailable and a run needs 1 to 10 rubric scores and issue lists',
  phases: [{ title: 'Score', detail: 'one agent per batch of designs; each views original and output' }],
}

// Run with args {file: <batches.json listing {id, dir, target} items>, batches: <its length>}.
const FILE = args.file
const N = args.batches

const PROMPT = `You are judging automatic design resizes for the Adobe Chameleon challenge.
A resize may change only element positions, sizes and font sizes; copy, fonts, colours and image
pixels are fixed, and inventing imagery is not allowed. The rubric:
- collisions: no unwanted overlaps. Intentional layering (decorative shapes, text on its own
  button) is fine; text over other text, images over text, or text on a busy area is not.
- readability: all text is large enough, has contrast, and nothing is cut off.
- brand: colours, fonts and the logo survive intact and in proportion; the logo is legible.
- intentional: things line up, spacing is even, the most important element still dominates,
  pictures keep their proportions. It should look like a designer made it.

For each item, original.png is the original design and output.png is the same design re-laid-out for a new canvas size.
Score output.png from 1 to 10 on each rubric row, using the original as the reference, plus overall, your holistic
score for how well it serves as the design at this size. List the concrete problems you see, most serious first.
Be strict and consistent: 9 or 10 means a designer would ship it untouched, 5 means clearly flawed.`

const SCHEMA = {
  type: 'object',
  properties: {
    scores: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          collisions: { type: 'integer' }, readability: { type: 'integer' }, brand: { type: 'integer' },
          intentional: { type: 'integer' }, overall: { type: 'integer' },
          issues: { type: 'array', items: { type: 'string' } },
        },
        required: ['id', 'collisions', 'readability', 'brand', 'intentional', 'overall', 'issues'],
      },
    },
  },
  required: ['scores'],
}

phase('Score')
const results = await parallel(Array.from({ length: N }, (_, k) => () =>
  agent(`${PROMPT}

Your items are batch number ${k} (0-based) of the JSON list in ${FILE}. Read that file and take element [${k}]: a list of items, each with an "id", a "dir" and the target canvas size "target".
For every item, view both images with the Read tool: <dir>/original.png, then <dir>/output.png. Small canvases such as 728x90 or 320x50 hold small text, so check readability, overlaps and edges closely.
Do not open any other files or folders. Return one score entry per item, using the item's id.`, {
    label: `score:batch${k}`,
    phase: 'Score',
    schema: SCHEMA,
  })
))
return results.filter(Boolean).flatMap(r => r.scores)

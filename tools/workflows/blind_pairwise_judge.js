export const meta = {
  name: 'blind-pairwise-judge',
  description: 'In-session blind pairwise judging of changed designs, both image orders in separate agents',
  whenToUse: 'When the Anthropic API judge is unavailable and candidate engine outputs must still be compared against the baseline',
  phases: [{ title: 'Judge', detail: 'one agent per batch of up to 6 comparisons; each views original, A and B renders' }],
}

// Run with args {file: <batches.json from `python -m tools.blind make`>, batches: <its length>}.
const BATCHES = args.file
const N = args.batches

const RUBRIC = `You are judging automatic design resizes for the Adobe Chameleon challenge.
A resize may change only element positions, sizes and font sizes; copy, fonts, colours and image pixels
are fixed, and inventing imagery is not allowed. The rubric:
- collisions: no unwanted overlaps. Intentional layering (decorative shapes, text on its own
  button) is fine; text over other text, images over text, or text on a busy area is not.
- readability: all text is large enough, has contrast, and nothing is cut off.
- brand: colours, fonts and the logo survive intact and in proportion; the logo is legible.
- intentional: things line up, spacing is even, the most important element still dominates,
  pictures keep their proportions. It should look like a designer made it.

For each comparison, original.png is the original design. A.png and B.png are two candidate resizes of it
to the same new canvas size. Pick the candidate a professional designer would ship, judging by the rubric.
Answer "tie" only when they are genuinely equal or the differences are negligible.`

const SCHEMA = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          winner: { type: 'string', enum: ['A', 'B', 'tie'] },
          reason: { type: 'string', description: 'one or two sentences naming the deciding differences' },
        },
        required: ['id', 'winner', 'reason'],
      },
    },
  },
  required: ['verdicts'],
}

phase('Judge')
const results = await parallel(Array.from({ length: N }, (_, k) => () =>
  agent(`${RUBRIC}

Your comparisons are batch number ${k} (0-based) of the JSON list in ${BATCHES}. Read that file and take element [${k}]: a list of items, each with an "id", a "dir" and the target canvas size "target".
For every item, view all three images in its dir with the Read tool: <dir>/original.png, then <dir>/A.png, then <dir>/B.png. Look carefully: small canvases such as 728x90 or 320x50 hold small text, so check readability, overlaps and edges closely.
Judge each item independently on its own merits. Do not open any other files or folders, and do not try to work out which image came from which program.
Return one verdict per item, using the item's id.`, {
    label: `judge:batch${k}`,
    phase: 'Judge',
    schema: SCHEMA,
  })
))
return results.filter(Boolean).flatMap(r => r.verdicts)

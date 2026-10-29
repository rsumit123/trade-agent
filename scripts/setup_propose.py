"""Step 3 proposer call (VM, inside the container: the API key lives there).

    ssh ssh-social "docker exec -i -w /app alphaagent-backend python3 - /app/data/bt_results/<campaign>/round_<k>_input.json" \
        < scripts/setup_propose.py

Reads {evidence, history, n_variants, round, live_stats}, asks Opus for variant
specs and prints a JSON array on stdout (usage on stderr). Places no trades and
touches no session data. The input never contains out-of-sample results.
"""
import json
import sys

sys.path.insert(0, "/app")
import anthropic  # noqa: E402

from agent.anthropic_text import response_text  # noqa: E402
from agent.setup_bt.proposer import build_prompt  # noqa: E402
from agent.setup_bt.variants import parse_specs  # noqa: E402

MODEL = "claude-opus-5-5"
inp = json.loads(open(sys.argv[1]).read())
prompt = build_prompt(inp["evidence"], inp.get("history", []), inp.get("n_variants", 8),
                      inp.get("round", 1), inp.get("live_stats", ""))
resp = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=16000,
                                             messages=[{"role": "user", "content": prompt}])
specs = parse_specs(response_text(resp))
u = resp.usage
print(f"model={MODEL} in={u.input_tokens} out={u.output_tokens} specs={len(specs)}", file=sys.stderr)
print(json.dumps(specs))

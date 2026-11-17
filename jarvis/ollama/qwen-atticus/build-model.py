"""Write the qwen-atticus Modelfile: same weights as qwen-agent, our no-thinking template, Qwen3.5 output parser."""
import pathlib
import subprocess

here = pathlib.Path(__file__).parent
froms = [l for l in subprocess.run(["ollama", "show", "qwen-agent", "--modelfile"], capture_output=True,
                                   text=True, check=True).stdout.splitlines() if l.startswith("FROM ")]
template = (here / "template.gotmpl").read_text()
(here / "Modelfile.nothink").write_text(
    "\n".join(froms)
    + '\nTEMPLATE """' + template + '"""\n'
    + "PARSER qwen3-coder\n"  # qwen3.5 parser assumes a thinking start; coder parses the same XML tool calls
    + "PARAMETER num_ctx 32768\nPARAMETER temperature 0.6\n"
    + 'PARAMETER stop "<|im_end|>"\n'
)
print("\n".join(froms))

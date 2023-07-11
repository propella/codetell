# CodeTell - An AI-powered tool that enables your code to tell its own story through automatic documentation generation.

Example: [codetell.md](./codetell.md)

## instructions

```
git clone git@github.com:propella/codetell.git
cd codetell
pyenv local 3.10
python -m venv .venv --prompt codetell
source .venv/bin/activate
make install

export OPENAI_API_KEY=(Your OpenAI API Key)
codetell .
```

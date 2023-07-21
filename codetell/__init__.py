import glob
from dataclasses import dataclass
from typing import Any, Iterator
import os
import argparse

import openai
from tenacity import (
    retry,
    wait_random_exponential,
    stop_after_attempt,
    retry_if_exception_type,
)


@dataclass
class Page:
    """A page"""

    text: str
    total_tokens: int
    source: str
    error: Any = None


MODEL_SHORT = "gpt-3.5-turbo"
MODEL_LONG = "gpt-3.5-turbo-16k"

INCLUDES = ["**/*.dart", "**/*.py", "**/*.js", "**/*.ts", "**/*.go", "**/*.rs"]
EXCLUDES = [
    "**/node_modules/**",
    "**/build/**",
    "**/dist/**",
    "**/target/**",
    "**/env/**",
]

Cache = dict[str, Page] | None


PROMPT_FUNCTIONAL = """Please write functional specification of the code below with the format in Markdown in %LANG%.
Do not include the source code in your response.

Format:
```
## (File name)

(Overview of the code)

## Description

(Explain in detail what the code does for beginners)

## Imports

(imported files in the project excluding names beginning with "package:")
```
"""

PROMPT_EXPLAIN = "Please explain what the code below does to end users in Markdown in %LANG%. Do not repeat the code."

PROMPT_REVIEW = """You are a professional programmer. Write code review for the code below in %LANG% following the format.

Format:
```
* Overview
    * (Summary of the code with your impression)
* What is good
    * (What is good about the code)
* What is bad
    * (What is bad about the code)
```
"""

PROMPT_API = "Please write API reference manual in Markdown in %LANG%. You must not include the source code in your response."


PROMPTS = {
    "functional": PROMPT_FUNCTIONAL,
    "explain": PROMPT_EXPLAIN,
    "review": PROMPT_REVIEW,
    "api": PROMPT_API,
}


def find_files(
    dir_name: str, includes: list[str] = INCLUDES, excludes: list[str] = EXCLUDES
) -> list[str]:
    """Return a list of source code in the directory.

    Args:
        dir_name: The directory to search
        includes: The list of patterns to include
        excludes: The list of patterns to exclude
    """
    files = set()

    # Including files
    for pattern in includes:
        files.update(glob.glob(pattern, root_dir=dir_name, recursive=True))

    # Excluding files
    for pattern in excludes:
        exclude_files = set(glob.glob(pattern, root_dir=dir_name, recursive=True))
        files.difference_update(exclude_files)

    # Sorted by depth and name
    sorted_files = sorted(list(files), key=lambda f: (f.count(os.sep), f))
    return sorted_files


def get_lang(lang: str | None) -> str:
    """Determine the language to use."""
    if lang:
        return lang
    if "CODETELL_LANG" in os.environ:
        return os.environ["CODETELL_LANG"]
    return "English"


def make_summary(
    dirname: str,
    title: str,
    filenames: list[str],
    lang: str | None = None,
    model: str = MODEL_SHORT,
) -> str:
    """Make summary of the project.

    Args:
        title: The title of the summary
        filenames: The filenames
        model: The model to use

    Returns:
        The summary
    """

    with open(f"{dirname}/README.md", "r", encoding="utf-8") as file:
        readme = file.read()

    filelist = "\n".join(f"* {filename}" for filename in filenames)

    prompt = f"""Generate a summary of the project in a few lines from the README.md file and the list of source code. Answer it in {get_lang(lang)}.

README:

{readme}

Files:

{filelist}

"""

    messages = [{"role": "user", "content": prompt}]
    try:
        response = openai.ChatCompletion.create(
            model=model, messages=messages, temperature=0
        )
    except openai.InvalidRequestError as error:
        # Retry with the long model
        if model == MODEL_SHORT:
            return make_summary(dirname, title, filenames, model=MODEL_LONG)
        else:
            return error.user_message

    if model == MODEL_SHORT and response["choices"][0]["finish_reason"] == "length":
        return make_summary(dirname, title, filenames, model=MODEL_LONG)

    summary = response["choices"][0]["message"]["content"]
    total_tokens = response["usage"]["total_tokens"]

    return f"""# {title}

{summary}

({total_tokens} tokens)

"""


@retry(
    wait=wait_random_exponential(min=1, max=40),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(openai.OpenAIError),
)
def make_description(
    dirname: str,
    filename: str,
    prompt: str = "explain",
    lang: str | None = None,
    model: str = MODEL_SHORT,
    cache: Cache = None,
) -> Page:
    """Ask openapi to generate document for the file.

    Args:
        dirname: The directory name
        filename: The filename
        prompt: One of "functional", "explain", "review", "api"
        model: The model to use
        cache: The cache to use

    Returns:
        Generated Page
    """

    fullpath = f"{dirname}/{filename}"
    if cache is not None and fullpath in cache:
        return cache[fullpath]

    with open(fullpath, "r", encoding="utf-8") as file:
        source = file.read()

    prompt_header = PROMPTS[prompt].replace("%LANG%", get_lang(lang))

    prompt_all = f"""{prompt_header}.

Filename:

{filename}

Code:

```
{source}
```
"""

    # print("Prompt:")
    # print(prompt_all)

    messages = [{"role": "user", "content": prompt_all}]
    try:
        response = openai.ChatCompletion.create(
            model=model, messages=messages, temperature=0
        )
    except openai.InvalidRequestError as error:
        # Retry with the long model
        if model == MODEL_SHORT:
            return make_description(dirname, filename, model=MODEL_LONG)
        else:
            return Page(text=error.user_message, total_tokens=0, source="")

    if model == MODEL_SHORT and response["choices"][0]["finish_reason"] == "length":
        return make_description(dirname, filename, model=MODEL_LONG)

    doc = response["choices"][0]["message"]["content"]
    page = Page(
        text=doc,
        total_tokens=response["usage"]["total_tokens"],
        source=source,
    )
    if cache is not None:
        cache[fullpath] = page
    return page


def make_page(
    dirname: str,
    filename: str,
    prompt: str = "explain",
    lang: str | None = None,
    cache: Cache = None,
) -> str:
    """Create a page from the file"""

    doc = make_description(dirname, filename, prompt=prompt, lang=lang, cache=cache)

    return f"""## `{filename}`

{doc.text}

({doc.total_tokens} tokens)

---

"""


class CodeTell:
    """Create documentation from source code"""

    def __init__(
        self,
        dirname: str,
        outfile: str | None = None,
        title: str | None = None,
        prompt: str = "explain",
        includes: list[str] = INCLUDES,
        excludes: list[str] = EXCLUDES,
        lang: str | None = None,
        dry_run: bool = False,
    ) -> None:
        self.dirname: str = dirname
        self.name: str = os.path.abspath(dirname).split("/")[-1]
        self.includes: list[str] = includes
        self.exclude: list[str] = excludes
        self.outfile: str = outfile or f"{self.name}.md"
        self.title: str = title or f"{self.name} Documentation"
        self.prompt: str = prompt
        self.lang: str | None = lang
        self.cache: dict[str, Page] = {}
        self.dry_run: bool = dry_run

    def sources(self) -> list[str]:
        """List of source files"""
        return find_files(self.dirname, includes=self.includes, excludes=self.exclude)

    def make_summary(self) -> str:
        """Return a summary of the project generated by AI"""
        return make_summary(self.dirname, self.title, self.sources(), lang=self.lang)

    def write_summary(
        self,
    ) -> None:
        """Write the summary of the project generated by AI"""
        if self.dry_run:
            return
        summary = self.make_summary()
        with open(self.outfile, "w", encoding="utf-8") as file:
            file.write(summary)

    def make_pages(
        self,
        up_to: int | None = None,
        cache: Cache = None,
    ) -> Iterator[str]:
        """Create a list of pages from the files in the directory"""

        for filename in self.sources()[:up_to]:
            print(f"{filename}... ")
            if self.dry_run:
                page = ""
            else:
                page = make_page(
                    self.dirname,
                    filename,
                    prompt=self.prompt,
                    lang=self.lang,
                    cache=cache,
                )
            yield page

    def write(
        self,
        up_to: int | None = None,
    ) -> None:
        """Write a page to the file"""

        self.write_summary()

        for page in self.make_pages(
            up_to=up_to,
            cache=self.cache,
        ):
            # display(Markdown(page))
            if not self.dry_run:
                with open(self.outfile, "a", encoding="utf-8") as file:
                    file.write(page)


def main() -> None:
    """The main function"""

    parser = argparse.ArgumentParser(
        prog="codetell",
        description="An AI-powered tool that enables your code to tell its own story through automatic documentation generation.",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="only tell what to do",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="only print summary",
    )
    parser.add_argument(
        "-l",
        "--lang",
        default="English",
        help="specify the language, if not, CODETELL_LANG environment variable or English is used",
    )
    parser.add_argument("dirname", help="the directory name")
    args = parser.parse_args()

    dirname = args.dirname

    writer = CodeTell(dirname, lang=args.lang, dry_run=args.dry_run)
    sources = writer.sources()
    print(f"Source directory: {dirname}")
    print(f"The number of source files: {len(sources)}")

    if args.summary:
        print(writer.make_summary())
    else:
        print(f"The output file: {writer.outfile}")
        writer.write()

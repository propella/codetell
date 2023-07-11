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


PROMPT_FUNCTIONAL = """Please write functional specification of the code below with the format in Markdown.
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

PROMPT_EXPLAIN = "Please explain what the code below does to end users in Markdown. Do not repeat the code."

PROMPT_REVIEW = """You are a professional programmer. Write code review for the code below following the format.

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

PROMPT_API = "Please write API reference manual in Markdown. You must not include the source code in your response."


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


def make_summary(
    dirname: str,
    name: str,
    filenames: list[str],
    lang: str = "English",
    model: str = MODEL_SHORT,
) -> str:
    """Make summary of the project.

    Args:
        name: The project name
        filenames: The filenames
        model: The model to use

    Returns:
        The summary
    """

    with open(f"{dirname}/README.md", "r", encoding="utf-8") as file:
        readme = file.read()

    filelist = "\n".join(f"* {filename}" for filename in filenames)

    prompt = f"""Generate a summary of the project in a few lines from the README.md file and the list of source code. Answer it in {lang}.

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
            return make_summary(dirname, name, filenames, model=MODEL_LONG)
        else:
            return error.user_message

    if model == MODEL_SHORT and response["choices"][0]["finish_reason"] == "length":
        return make_summary(dirname, name, filenames, model=MODEL_LONG)

    summary = response["choices"][0]["message"]["content"]
    total_tokens = response["usage"]["total_tokens"]

    return f"""# {name} project

{summary}

({total_tokens} tokens)

"""


@retry(
    wait=wait_random_exponential(min=1, max=40),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(openai.APIError),
)
def make_description(
    dirname: str,
    filename: str,
    prompt: str = "explain",
    lang: str = "English",
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
        contents = file.read()

    prompt_header = PROMPTS[prompt]

    prompt_all = f"""In {lang}, {prompt_header}.

Filename:

{filename}

Code:

```
{contents}
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
            return Page(text=error.user_message, total_tokens=0)

    if model == MODEL_SHORT and response["choices"][0]["finish_reason"] == "length":
        return make_description(dirname, filename, model=MODEL_LONG)

    doc = response["choices"][0]["message"]["content"]
    page = Page(
        text=doc,
        total_tokens=response["usage"]["total_tokens"],
    )
    if cache is not None:
        cache[fullpath] = page
    return page


def make_page(
    dirname: str,
    filename: str,
    prompt: str = "explain",
    lang: str = "English",
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
        self, dirname: str, lang: str = "English", dry_run: bool = False
    ) -> None:
        self.dirname: str = dirname
        self.name: str = os.path.abspath(dirname).split("/")[-1]
        self.outfile: str = f"{self.name}.md"
        self.lang: str = lang
        self.cache: dict[str, Page] = {}
        self.dry_run: bool = dry_run

    def sources(self) -> list[str]:
        """List of source files"""
        return find_files(self.dirname)

    def make_summary(self) -> str:
        """Return a summary of the project generated by AI"""
        return make_summary(self.dirname, self.name, self.sources(), lang=self.lang)

    def write_summary(self) -> None:
        """Write the summary of the project generated by AI"""
        if self.dry_run:
            return
        summary = self.make_summary()
        with open(self.outfile, "w", encoding="utf-8") as file:
            file.write(summary)

    def make_pages(
        self,
        prompt: str = "explain",
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
                    self.dirname, filename, prompt=prompt, lang=self.lang, cache=cache
                )
            yield page

    def write(
        self,
        title: str,
        prompt: str = "explain",
        up_to: int | None = None,
    ) -> None:
        """Write a page to the file"""

        # display(Markdown(f"# {title}"))
        # with open(self.outfile, "w", encoding="utf-8") as file:
        #    file.write(f"# {title}\n")

        for page in self.make_pages(
            prompt=prompt,
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
        help="specify the language",
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
        writer.write_summary()
        writer.write(f"{writer.name} Documentation", prompt="explain")

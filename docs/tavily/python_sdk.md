# Quickstart

> Integrate Tavily's powerful APIs natively in your Python apps.

<Tip>
  Looking for the Python SDK Reference? Head to our [Python SDK Reference](/sdk/python/reference) and learn how to use `tavily-python`.
</Tip>

## Introduction

The Python SDK allows for easy interaction with the Tavily API, offering the full range of our search functionality directly from your Python programs. Easily integrate smart search capabilities into your applications, harnessing Tavily's powerful search features.

<CardGroup cols={2}>
  <Card title="GitHub" icon="github" href="https://github.com/tavily-ai/tavily-python" horizontal>
    `/tavily-ai/tavily-python`

    <img noZoom src="https://img.shields.io/github/stars/tavily-ai/tavily-python?style=social" alt="GitHub Repo stars" />
  </Card>

  <Card title="PyPI" icon="python" href="https://pypi.org/project/tavily-python" horizontal>
    `tavily-python`

    <img noZoom src="https://img.shields.io/pypi/dm/tavily-python" alt="PyPI downloads" />
  </Card>
</CardGroup>

## Quickstart

Get started with our Python SDK in less than 5 minutes!

<Card icon="key" href="https://app.tavily.com" title="Get your free API key" horizontal>
  You get 1,000 free API Credits every month. **No credit card required.**
</Card>

### Installation

You can install the Tavily Python SDK using the following:

```bash  theme={null}
pip install tavily-python
```

### Usage

With Tavily's Python SDK, you can search the web in only 4 lines of code:

```python  theme={null}
from tavily import TavilyClient

tavily_client = TavilyClient(api_key="tvly-YOUR_API_KEY")
response = tavily_client.search("Who is Leo Messi?")

print(response)
```

You can also easily extract content from URLs:

```python  theme={null}
from tavily import TavilyClient

tavily_client = TavilyClient(api_key="tvly-YOUR_API_KEY")
response = tavily_client.extract("https://en.wikipedia.org/wiki/Lionel_Messi")

print(response)
```

Tavily also allows you to perform a smart crawl starting at a given URL.

<Tip>
  Our agent-first crawl endpoint is currently in **open beta**. Please repost any issues you encounter on our [community page](https://community.tavily.com).
</Tip>

```python  theme={null}
from tavily import TavilyClient

tavily_client = TavilyClient(api_key="tvly-YOUR_API_KEY")
response = tavily_client.crawl("https://docs.tavily.com", instructions="Find all pages on the Python SDK")

print(response)
```

These examples are very simple, and you can do so much more with Tavily!

## Features

Our Python SDK supports the full feature range of our [REST API](/api-reference), and more. We offer both a synchronous and an asynchronous client, for increased flexibility.

* The `search` function lets you harness the full power of Tavily Search.
* The `extract` function allows you to easily retrieve web content with Tavily Extract.
* The `crawl` and `map`functions allow you to intelligently traverse websites and extract content.

For more details, head to the [Python SDK Reference](/sdk/python/reference).

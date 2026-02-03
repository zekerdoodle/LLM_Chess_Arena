# Quickstart

> Get started with Perplexity's Search API to retrieve ranked web search results with advanced filtering and customization options.

## Overview

<Card title="Try Our New Interactive Playground" href="https://perplexity.ai/account/api/playground/search" horizontal="True" cta="Launch Playground">
  Test queries and explore parameters in real-time. **No API key required.**
</Card>

The Search API gives ranked results from Perplexity's continuously refreshed index.

<Info>
  We recommend using our official SDKs for a more convenient and type-safe way to interact with the Search API.
</Info>

## Installation

Install the SDK for your preferred language:

<CodeGroup>
  ```bash Python theme={null}
  pip install perplexityai
  ```

  ```bash TypeScript/JavaScript theme={null}
  npm install @perplexity-ai/perplexity_ai
  ```
</CodeGroup>

## Basic Usage

Start with a basic search query to get relevant web results:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  search = client.search.create(
      query="latest AI developments 2024",
      max_results=5,
      max_tokens_per_page=1024
  )

  for result in search.results:
      print(f"{result.title}: {result.url}")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  const search = await client.search.create({
      query: "latest AI developments 2024",
      maxResults: 5,
      maxTokensPerPage: 1024
  });

  for (const result of search.results) {
      console.log(`${result.title}: ${result.url}`);
  }
  ```

  ```javascript JavaScript theme={null}
  const Perplexity = require('@perplexity-ai/perplexity_ai');

  const client = new Perplexity();

  async function main() {
      const search = await client.search.create({
          query: "latest AI developments 2024",
          maxResults: 5,
          maxTokensPerPage: 1024
      });

      for (const result of search.results) {
          console.log(`${result.title}: ${result.url}`);
      }
  }

  main();
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer YOUR_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "latest AI developments 2024",
      "max_results": 5,
      "max_tokens_per_page": 1024
    }' | jq
  ```
</CodeGroup>

<Accordion title="Response">
  ```json  theme={null}
  {
    "results": [
      {
        "title": "2024: A year of extraordinary progress and advancement in AI - Google Blog",
        "url": "https://blog.google/technology/ai/2024-ai-extraordinary-progress-advancement/",
        "snippet": "## Relentless innovation in models, products and technologies\n\n2024 was a year of experimenting, fast shipping, and putting our latest technologies in the hands of developers.\n\nIn December 2024, we released the first models in our Gemini 2.0 experimental series  AI models designed for the agentic era. First out of the gate was Gemini 2.0 Flash, our workhorse model, followed by prototypes from the frontiers of our agentic research including: an updated Project Astra, which explores the capabilities of a universal AI assistant; Project Mariner, an early prototype capable of taking actions in Chrome as an experimental extension; and Jules, an AI-powered code agent. We're looking forward to bringing Gemini 2.0s powerful capabilities to our flagship products  in Search, we've already started testing in AI Overviews, which are now used by over a billion people to ask new types of questions.\n\nWe also released Deep Research, a new agentic feature in Gemini Advanced that saves people hours of research work by creating and executing multi-step plans for finding answers to complicated questions; and introduced Gemini 2.0 Flash Thinking Experimental, an experimental model that explicitly shows its thoughts.\n\nThese advances followed swift progress earlier in the year, from incorporating Gemini's capabilities into more Google products to the release of Gemini 1.5 Pro and Gemini 1.5 Flash  a model optimized for speed and efficiency. 1.5 Flash's compact size made it more cost-efficient to serve, and in 2024 it became our most popular model for developers.... ## The architecture of intelligence: advances in robotics, hardware and computing\n\nAs our multimodal models become more capable and gain a better understanding of the world and its physics, they are making possible incredible new advances in robotics and bringing us closer to our goal of ever-more capable and helpful robots.\n\nWith ALOHA Unleashed, our robot learned to tie a shoelace, hang a shirt, repair another robot, insert a gear and even clean a kitchen.\n\nAt the beginning of the year, we introduced AutoRT, SARA-RT and RT-Trajectory, extensions of our Robotics Transformers work intended to help robots better understand and navigate their environments, and make decisions faster. We also published ALOHA Unleashed, a breakthrough in teaching robots on how to use two robotic arms in coordination, and DemoStart, which uses a reinforcement learning algorithm to improve real-world performance on a multi-fingered robotic hand by using simulations.\n\nRobotic Transformer 2 (RT-2) is a novel vision-language-action model that learns from both web and robotics data.\n\nBeyond robotics, our AlphaChip reinforcement learning method for accelerating and improving chip floorplanning is transforming the design process for chips found in data centers, smartphones and more. To accelerate adoption of these techniques, we released a pre-trained checkpoint to enable external parties to more easily make use of the AlphaChip open source release for their own chip designs. And we made Trillium, our sixth-generation and most performant TPU to date, generally available to Google Cloud customers. Advances in computer chips have accelerated AI. And now, AI can return the favor.... We are exploring how machine learning can help medical fields struggling with access to imaging expertise, such as radiology, dermatology and pathology. In the past year, we released two research tools, Derm Foundation and Path Foundation, that can help develop models for diagnostic tasks, image indexing and curation and biomarker discovery and validation. We collaborated with physicians at Stanford Medicine on an open-access, inclusive Skin Condition Image Network (SCIN) dataset. And we unveiled CT Foundation, a medical imaging embedding tool used for rapidly training models for research.\n\nWith regard to learning, we explored new generative AI tools to support educators and learners. We introduced LearnLM, our new family of models fine-tuned for learning and used it to enhance learning experiences in products like Search, YouTube and Gemini; a recent report showed LearnLM outperformed other leading AI models. We also made it available to developers as an experimental model in AI Studio. Our new conversational learning companion, LearnAbout, uses AI to help you dive deeper into any topic you're curious about, while Illuminate lets you turn content into engaging AI-generated audio discussions.\n\nIn the fields of disaster forecasting and preparedness, we announced several breakthroughs. We introduced GenCast, our new high-resolution AI ensemble model, which improves day-to-day weather and extreme events forecasting across all possible weather trajectories. We also introduced our NeuralGCM model, able to simulate over 70,000 days of the atmosphere in the time it would take a physics-based model to simulate only 19 days. And GraphCast won the 2024 MacRobert Award for engineering innovation.",
        "date": "2025-01-23",
        "last_updated": "2025-09-25"
      },
      {
        "title": "The 2025 AI Index Report | Stanford HAI",
        "url": "https://hai.stanford.edu/ai-index/2025-ai-index-report",
        "snippet": "Read the translation\n\nIn 2023, researchers introduced new benchmarksMMMU, GPQA, and SWE-benchto test the limits of advanced AI systems. Just a year later, performance sharply increased: scores rose by 18.8, 48.9, and 67.3 percentage points on MMMU, GPQA, and SWE-bench, respectively. Beyond benchmarks, AI systems made major strides in generating high-quality video, and in some settings, language model agents even outperformed humans in programming tasks with limited time budgets.\n\nFrom healthcare to transportation, AI is rapidly moving from the lab to daily life. In 2023, the FDA approved 223 AI-enabled medical devices, up from just six in 2015. On the roads, self-driving cars are no longer experimental: Waymo, one of the largest U.S. operators, provides over 150,000 autonomous rides each week, while Baidu's affordable Apollo Go robotaxi fleet now serves numerous cities across China.\n\nIn 2024, U.S. private AI investment grew to $109.1 billionnearly 12 times China's $9.3 billion and 24 times the U.K.'s $4.5 billion. Generative AI saw particularly strong momentum, attracting $33.9 billion globally in private investmentan 18.7% increase from 2023. AI business usage is also accelerating: 78% of organizations reported using AI in 2024, up from 55% the year before. Meanwhile, a growing body of research confirms that AI boosts productivity and, in most cases, helps narrow skill gaps across the workforce.... In 2024, U.S.-based institutions produced 40 notable AI models, significantly outpacing China's 15 and Europe's three. While the U.S. maintains its lead in quantity, Chinese models have rapidly closed the quality gap: performance differences on major benchmarks such as MMLU and HumanEval shrank from double digits in 2023 to near parity in 2024. Meanwhile, China continues to lead in AI publications and patents. At the same time, model development is increasingly global, with notable launches from regions such as the Middle East, Latin America, and Southeast Asia.\n\nAI-related incidents are rising sharply, yet standardized RAI evaluations remain rare among major industrial model developers. However, new benchmarks like HELM Safety, AIR-Bench, and FACTS offer promising tools for assessing factuality and safety. Among companies, a gap persists between recognizing RAI risks and taking meaningful action. In contrast, governments are showing increased urgency: In 2024, global cooperation on AI governance intensified, with organizations including the OECD, EU, U.N., and African Union releasing frameworks focused on transparency, trustworthiness, and other core responsible AI principles.\n\nIn countries like China (83%), Indonesia (80%), and Thailand (77%), strong majorities see AI products and services as more beneficial than harmful. In contrast, optimism remains far lower in places like Canada (40%), the United States (39%), and the Netherlands (36%). Still, sentiment is shifting: since 2022, optimism has grown significantly in several previously skeptical countriesincluding Germany (+10%), France (+10%), Canada (+8%), Great Britain (+8%), and the United States (+4%).... Driven by increasingly capable small models, the inference cost for a system performing at the level of GPT-3.5 dropped over 280-fold between November 2022 and October 2024. At the hardware level, costs have declined by 30% annually, while energy efficiency has improved by 40% each year. Open-weight models are also closing the gap with closed models, reducing the performance difference from 8% to just 1.7% on some benchmarks in a single year. Together, these trends are rapidly lowering the barriers to advanced AI.\n\nIn 2024, U.S. federal agencies introduced 59 AI-related regulationsmore than double the number in 2023and issued by twice as many agencies. Globally, legislative mentions of AI rose 21.3% across 75 countries since 2023, marking a ninefold increase since 2016. Alongside growing attention, governments are investing at scale: Canada pledged $2.4 billion, China launched a $47.5 billion semiconductor fund, France committed �109 billion, India pledged $1.25 billion, and Saudi Arabia's Project Transcendence represents a $100 billion initiative.\n\nTwo-thirds of countries now offer or plan to offer K12 CS educationtwice as many as in 2019with Africa and Latin America making the most progress. In the U.S., the number of graduates with bachelor's degrees in computing has increased 22% over the last 10 years. Yet access remains limited in many African countries due to basic infrastructure gaps like electricity. In the U.S., 81% of K12 CS teachers say AI should be part of foundational CS education, but less than half feel equipped to teach it.",
        "date": "2024-09-10",
        "last_updated": "2025-09-25"
      },
      {
        "title": "The State of AI: Global survey - McKinsey",
        "url": "https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai",
        "snippet": "**Organizations are starting to make** organizational changes designed to generate future value from gen AI, and large companies are leading the way. The latest McKinsey Global Survey on AI finds that organizations are beginning to take steps that drive bottom-line impactfor example, redesigning workflows as they deploy gen AI and putting senior leaders in critical roles, such as overseeing AI governance. The findings also show that organizations are working to mitigate a growing set of gen-AI-related risks and are hiring for new AI-related roles while they retrain employees to participate in AI deployment. Companies with at least $500 million in annual revenue are changing more quickly than smaller organizations. Overall, the use of AIthat is, gen AI as well as analytical AIcontinues to build momentum: More than three-quarters of respondents now say that their organizations use AI in at least one business function. The use of gen AI in particular is rapidly increasing.\n\n## How companies are organizing their gen AI deploymentand who's in charge... ## AI is shifting the skills that organizations need\n\nThis survey also examines the state of AI-related hiring and other ways AI affects the workforce. Respondents working for organizations that use AI are about as likely as they were in the early 2024 survey to say their organizations hired individuals for AI-related roles in the past 12 months. The only roles that differ this year are data-visualization and design specialists, which respondents are significantly less likely than in the previous survey to report hiring. The findings also indicate several new risk-related roles that are becoming part of organizations' AI deployment processes. Thirteen percent of respondents say their organizations have hired AI compliance specialists, and 6 percent report hiring AI ethics specialists. Respondents at larger companies are more likely than their peers at smaller organizations to report hiring a broad range of AI-related roles, with the largest gaps seen in hiring AI data scientists, machine learning engineers, and data engineers.\n\nRespondents continue to see these roles as largely challenging to fill, though a smaller share of respondents than in the past two years describe hiring for many roles as "difficult" or "very difficult" (Exhibit 5). One exception is AI data scientists, who will continue to be in high demand in the year ahead: Half of respondents whose organizations use AI say their employers will need more data scientists than they have now.... *AI*including gen AI and analytical AIC-level executives are more likely than middle managers to predict increasing head count.\n\nLooking at the expected effects of gen AI deployment by business function, respondents most often predict decreasing head count in service operations, such as customer care and field services, as well as in supply chain and inventory management (Exhibit 7). In IT and product development, however, respondents are more likely to expect increasing than decreasing head count.... ## AI use continues to climb\n\nReported use of AI increased in 2024.\n\n3 In the latest survey, 78 percent of respondents say their organizations use AI in at least one business function, up from 72 percent in early 2024 and 55 percent a year earlier (Exhibit 8). Respondents most often report using the technology in the IT and marketing and sales functions, followed by service operations. The business function that saw the largest increase in AI use in the past six months is IT, where the share of respondents reporting AI use jumped from 27 percent to 36 percent.\n\nOrganizations are also using AI in more business functions than in the previous State of AI survey. For the first time, most survey respondents report the use of AI in more than one business function (Exhibit 9). Responses show organizations using AI in an average of three business functionsan increase from early 2024, but still a minority of functions.\n\nThe use of gen AI has seen a similar jump since early 2024: 71 percent of respondents say their organizations regularly use gen AI in at least one business function, up from 65 percent in early 2024.\n\n4 (Individuals' use of gen AI has also grown. See sidebar, "C-level executives are using gen AI more than others.") Responses show that organizations are most often using gen AI in marketing and sales, product and service development, service operations, and software engineeringbusiness functions where gen AI deployment would likely generate the most value, according to previous McKinsey researchas well as in IT.... ## About the research\n\nThe online survey was in the field from July 16 to July 31, 2024, and garnered responses from 1,491 participants in 101 nations representing the full range of regions, industries, company sizes, functional specialties, and tenures. Forty-two percent of respondents say they work for organizations with more than $500 million in annual revenues. To adjust for differences in response rates, the data are weighted by the contribution of each respondent's nation to global GDP.",
        "date": "2025-03-12",
        "last_updated": "2025-09-25"
      },
      {
        "title": "The Top Artificial Intelligence Trends - IBM",
        "url": "https://www.ibm.com/think/insights/artificial-intelligence-trends",
        "snippet": "Approaching the midpoint of 2025, we can look back at the prevailing artificial intelligence trends of the year so farand look ahead to what the rest of the year might bring.\n\nGiven the breadth and depth of AI development, no roundup of AI trends can hope to be exhaustive. This piece is no exception. We've narrowed things down to a list of 10: 5 developments that have driven the first half of the year, and 5 more that we expect to play a major role in the months to come.\n\nTrends in AI are driven not only by advancements in AI models and algorithms themselves, but by the ever-expanding array of use cases to which generative AI (gen AI) capabilities are being applied. As models grow more capable, versatile and efficient, so too do the AI applications, AI tools and other AI-powered workflows they enable. A true understanding of how today's AI ecosystem is evolving therefore requires a contextual understanding of the causes and effects of machine learning breakthroughs.\n\nThis article primarily explores ongoing trends whose real-world impact might be realized on a horizon of months: in other words, trends with tangible impact mostly on or in the year 2025. There are, of course, other AI initiatives that are more evergreen and familiar. For example, though there has been recent movement on fully self-driving vehicles in isolated pocketsrobotaxi pilots have been launched in a handful of U.S. cities, with additional trials abroad in Oslo, Geneva and 16 Chinese citiesthey're likely still years away from ubiquity.... One study estimates the pace of algorithmic improvement at roughly 400% per year: in other words, today's results can be achieved a year later using one fourth of the computeand that's\n\n*without *accounting for simultaneous improvements in computing (see: Moore's Law) or synthetic training data. The original GPT-4, rumored to have around 1.8 trillion parameters, 1 achieved a score of 67% on HumanEval, a popular benchmark for coding performance. IBM Granite 3.3 2B Instruct, released 2 years later and *900 times smaller, *achieved a score of 80.5%. 2\n\nThis exponential expansion of model economy, more than anything, is what empowers the emerging era of AI agents. Large language models (LLMs) are becoming more practical even faster than they're becoming more capable, which enables the deployment of complex multi-agent systems in which a cadre of models can plan, execute and coordinate on complex tasks autonomouslywithout skyrocketing inference costs.\n\nThe release of OpenAI's o1 introduced a new avenue for increasing model performance. Its head-turning improvement over prior state-of-the-art performance on highly technical math and coding benchmarks initiated an arms race in so-called "reasoning models." Their enhanced performance on tasks requiring logical decision-making figures to play an important role in the development of agentic AI. But as is often the case with AI technology, the initial frenzy over raw performance has more recently given way to a search for the most practical implementation.... When it comes to transformers or mamba, the future of AI is not probably an "either/or" situation: in fact, research suggests that a hybrid of the two is better than either on their own. Several mamba or hybrid mamba/transformer models have been released in the past year. Most have been academic research-only models, with notable exceptions including Mistral AI's Codestral Mamba and AI2I's hybrid Jamba series. More recently, the upcoming IBM Granite 4.0 series will be using a hybrid of transformer and Mamba-2 architectures.\n\nMost importantly, the reduced hardware requirements of Mamba and hybrid models will significantly reduce hardware costs, which in turn will help continue to democratize AI access.\n\nThe advent of multimodal AI models marked the expansion of LLMs beyond text, but the next frontier of AI development aims to bring those multimodal abilities into the physical world.\n\nThis emerging field largely falls under the heading of "Embodied AI." Venture capital firms are increasingly pouring funding into startups pursuing advanced, generative AI-driven humanoid robotics, such as Skild AI, Physical Intelligence, and 1X Technologies.\n\nAnother stream of research is focusing on "world models" that aim to model real-world interactions directly and holistically, rather than indirectly and discretely through the mediums of language, image and video data. World Labs, a startup headed by Stanford's Fei-Fei Lifamed for, among other things, the ImageNet dataset that helped pave a path for modern computer visionraised USD 230 million at the end of last year.",
        "date": "2025-05-21",
        "last_updated": "2025-09-25"
      },
      {
        "title": "AI Pulse: Top AI Trends from 2024 - A Look Back | Trend Micro (US)",
        "url": "https://www.trendmicro.com/en_us/research/25/a/top-ai-trends-from-2024-review.html",
        "snippet": "AI Comes Into Its Own\n\n2024 may go down as the year AI stopped being a technological novelty and becamemore consequentiallya Fact of Life. Big names like Microsoft, Salesforce, and Intuit built AI into mainstream enterprise solutions; specialized AI apps and services sprung up for everything from copywriting to data analysis; and governments, think tanks, and regulators poured effort into setting up meaningful guardrails for AI development and use. Meanwhile, bad actors made good on finding new ways to dupe, intimidate, and extort using AI tools.\n\nThis special issue of\n\n*AI Pulse* looks back over the AI trends in 2024 and what they mean for the year ahead.\n\nAI Trends in 2024\n\n**AI Advances by Leaps and Bounds\n\n**Our previous\n\n*AI Pulse*was dedicated mostly to agentic AIfor good reason. Autonomous, cooperative machine-based problem solving is widely seen as an essential step along the path to artificial general intelligence (AGI). All the big AI players spotlighted R&D efforts in the agentic arena over the course of 2024and non-AI players moved in to offer AI agents as a service (AIAaaS).\n\n**Teaching computers to use computers\n\n**One of the year's big agentic releases was the public beta of Computer Use for Anthropic's Claude 3.5 Sonnet model. As the name suggests, Computer Use allows Claude 3.5 Sonnet to use a computer by 'looking' at the screen, manipulating the cursor, clicking on links, and entering text. Other developers are also working on web-savvy agents, though assessing performance at scale is a widely recognized challenge. The research company ServiceNow is aiming to change that with its AgentLab offeringan open-source Python package launched in December that's capable of running large-scale web agent experiments in parallel across a diversity of online environments.... **From RAGs to AI riches\n\n**AI systems need relevant data to solve problems effectively. Retrieval-augmented generation (RAG) provides that by giving systems access to contextually significant information instead of broad, unfocused data sets. On its own, RAG has been found to reduce AI hallucinations and outperform alternative approaches such as long-context transformers and fine-tuning. Combining RAG with fine-tuning produces even better results.\n\nAnthropic announced its own spin on RAG earlier this fall with "contextual retrieval"said to make information retrieval more successfuland a new Model Context Protocol (MCP) for connecting AI assistants to data systems in a reliable and scalable way.\n\nTrend Micro has found RAG isn't without its risks. Exposed vector stores and LLM-hosting platforms can give way to data leaks and unauthorized access. Security issues such as data validation bugs and denial-of-service (DoS) attacks are common across RAG components. Beyond authentication, Trend recommends implementing transport layer security (TLS) encryption and zero-trust networking to prevent unauthorized access and manipulation.\n\n**'Smallifying' AI models\n\n**Hand in hand with the shift to agentic AI is the need for smaller, nimbler, faster models purpose-built for specific tasks. Again, lots of work went into this in 2024. In October, Meta released updates to its Llama AI model that are as much as four times faster and 56% smaller than their precursors, enabling sophisticated AI features on devices as small as smartphones. And Nvidia released its Nemotron-Mini-4B Instruct small language model (SLM), which gets VRAM usage down to about 2GB for far faster speeds than LLMs.... MIT also contributed to the effort to track AI risks. In August, it launched a public AI Risk Repository with more than 700 risks based on over 40 different frameworks, with citations and risk taxonomies.\n\nAI Can do Good, Too\n\nWhile it's important to be clear about the risks of AI, it's just as important to stay mindful of the benefitsand a number of efforts sought to highlight those positive capabilities in 2024.\n\n**Beating the bad guys to it\n\n**Using AI to discover vulnerabilities and exploits got a fair bit of attention throughout the year. While AI isn't always needed, in situations where complexity is high and unknowns abound, it can deliver excellent results. The Frontier Model Forum found vulnerability discovery and patching is an emerging area of AI strength, due partly to increased use of coding examples in post-training and partly because of expanding context windows. AI can also support open-source intelligence gathering and reporting through real-time monitoring and analysis, trend identification, and more.\n\nAs predicted by Trend Micro for 2025, agentic AI could expand on those capabilities with a combination of tooling, data, and planning that reduce the amount of human brain time involved. Combining agentic use of reverse-engineering tools such as Ida, Ghidra, and Binary Ninja with code similarity, architectural RAG, and algorithm identification for compiled code could be a powerful lever in the cybersecurity arms race.",
        "date": "2025-01-03",
        "last_updated": "2025-09-25"
      }
    ],
    "id": "e38104d5-6bd7-4d82-bc4e-0a21179d1f77"
  }
  ```
</Accordion>

<Info>
  The `max_results` parameter accepts values from 1 to 20, with a default maximum of 10 results per search.
</Info>

## Regional Search

You can refine your search results by specifying a country to get more geographically relevant results:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  # Search for results from a specific country
  search = client.search.create(
      query="government policies on renewable energy",
      country="US",  # ISO country code
      max_results=5
  )

  for result in search.results:
      print(f"{result.title}: {result.url}")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  // Search for results from a specific country
  const search = await client.search.create({
      query: "government policies on renewable energy",
      country: "US", // ISO country code
      maxResults: 5
  });

  for (const result of search.results) {
      console.log(`${result.title}: ${result.url}`);
  }
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer YOUR_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "government policies on renewable energy",
      "country": "US",
      "max_results": 5
    }' | jq
  ```
</CodeGroup>

<Tip>
  Use ISO 3166-1 alpha-2 country codes (e.g., "US", "GB", "DE", "JP") to target specific regions. This is particularly useful for queries about local news, regulations, or region-specific information.
</Tip>

## Multi-Query Search

Execute multiple related queries in a single request for comprehensive research:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  search = client.search.create(
      query=[
          "artificial intelligence trends 2024",
          "machine learning breakthroughs recent",
          "AI applications in healthcare"
      ],
      max_results=5
  )

  # Access results for each query
  for i, query_results in enumerate(search.results):
      print(f"Results for query {i+1}:")
      for result in query_results:
          print(f"  {result.title}: {result.url}")
      print("---")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  const search = await client.search.create({
      query: [
          "artificial intelligence trends 2024",
          "machine learning breakthroughs recent", 
          "AI applications in healthcare"
      ],
      maxResults: 5
  });

  // Access results for each query
  search.results.forEach((queryResults, i) => {
      console.log(`Results for query ${i+1}:`);
      queryResults.forEach(result => {
          console.log(`  ${result.title}: ${result.url}`);
      });
      console.log("---");
  });
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer YOUR_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": [
        "artificial intelligence trends 2024",
        "machine learning breakthroughs recent",
        "AI applications in healthcare"
      ],
      "max_results": 5
    }' | jq
  ```
</CodeGroup>

<Tip>
  Multi-query search is ideal for research tasks where you need to explore different angles of a topic. Each query is processed independently, giving you comprehensive coverage.
</Tip>

<Info>
  You can include up to 5 queries in a single multi-query request for efficient batch processing.
</Info>

## Domain Filtering

The `search_domain_filter` parameter allows you to limit search results to specific domains (allowlist) or exclude certain domains (denylist) for focused research. The filter works in two modes:

* **Allowlist mode**: Include only specified domains (no `-` prefix)
* **Denylist mode**: Exclude specified domains (use `-` prefix)

**Note**: You can use either allowlist or denylist mode, but not both simultaneously in the same request.

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  search = client.search.create(
      query="climate change research",
      search_domain_filter=[
          "science.org",
          "pnas.org",
          "cell.com"
      ],
      max_results=10
  )

  for result in search.results:
      print(f"{result.title}: {result.url}")
      print(f"Published: {result.date}")
      print("---")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  const search = await client.search.create({
      query: "climate change research",
      searchDomainFilter: [
          "science.org",
          "pnas.org",
          "cell.com"
      ],
      maxResults: 10
  });

  for (const result of search.results) {
      console.log(`${result.title}: ${result.url}`);
      console.log(`Published: ${result.date}`);
      console.log("---");
  }
  ```

  ```javascript JavaScript theme={null}
  const Perplexity = require('@perplexity-ai/perplexity_ai');

  const client = new Perplexity();

  async function main() {
      const search = await client.search.create({
          query: "climate change research",
          searchDomainFilter: [
              "science.org",
              "pnas.org",
              "cell.com"
          ],
          maxResults: 10
      });

      for (const result of search.results) {
          console.log(`${result.title}: ${result.url}`);
          console.log(`Published: ${result.date}`);
          console.log("---");
      }
  }

  main();
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer YOUR_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "climate change research",
      "search_domain_filter": [
        "science.org",
        "pnas.org",
        "cell.com"
      ],
      "max_results": 10
    }' | jq
  ```
</CodeGroup>

<Warning>
  You can add a maximum of 20 domains to the `search_domain_filter` list. The filter works in either allowlist mode (include only) or denylist mode (exclude), but not both simultaneously.
</Warning>

### Denylisting Example

You can also exclude specific domains from search results:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  # Exclude social media sites from search results
  search = client.search.create(
      query="renewable energy innovations",
      search_domain_filter=[
          "-pinterest.com",
          "-reddit.com",
          "-quora.com"
      ],
      max_results=10
  )

  for result in search.results:
      print(f"{result.title}: {result.url}")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  // Exclude social media sites from search results
  const search = await client.search.create({
      query: "renewable energy innovations",
      searchDomainFilter: [
          "-pinterest.com",
          "-reddit.com",
          "-quora.com"
      ],
      maxResults: 10
  });

  for (const result of search.results) {
      console.log(`${result.title}: ${result.url}`);
  }
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer YOUR_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "renewable energy innovations",
      "search_domain_filter": [
        "-pinterest.com",
        "-reddit.com",
        "-quora.com"
      ],
      "max_results": 10
    }' | jq
  ```
</CodeGroup>

## Language Filtering

The `search_language_filter` parameter allows you to filter search results by language using ISO 639-1 language codes:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  # Search for English, French, and German language results
  search = client.search.create(
      query="latest AI news",
      search_language_filter=["en", "fr", "de"],
      max_results=10
  )

  for result in search.results:
      print(f"{result.title}: {result.url}")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  // Search for English, French, and German language results
  const multiLangSearch = await client.search.create({
      query: "latest AI news",
      searchLanguageFilter: ["en", "fr", "de"],
      maxResults: 10
  });

  for (const result of search.results) {
      console.log(`${result.title}: ${result.url}`);
  }
  ```

  ```javascript JavaScript theme={null}
  const Perplexity = require('@perplexity-ai/perplexity_ai');

  const client = new Perplexity();

  async function main() {
      // Search for English, French, and German language results
      const search = await client.search.create({
          query: "latest AI news",
          searchLanguageFilter: ["en", "fr", "de"],
          maxResults: 10
      });

      for (const result of search.results) {
          console.log(`${result.title}: ${result.url}`);
      }
  }

  main();
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer YOUR_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "latest AI news",
      "search_language_filter": ["en", "fr", "de"],
      "max_results": 10
    }' | jq
  ```
</CodeGroup>

<Warning>
  Language codes must be valid 2-letter ISO 639-1 codes (e.g., "en", "ru", "fr"). You can add a maximum of 10 language codes per request.
</Warning>

## Content Extraction Control

The `max_tokens_per_page` parameter controls how much content is extracted from each webpage during search processing. This allows you to balance between comprehensive content coverage and processing efficiency.

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  # Extract more content for comprehensive analysis
  detailed_search = client.search.create(
      query="artificial intelligence research methodology",
      max_results=5,
      max_tokens_per_page=2048
  )

  # Use default extraction for faster processing
  quick_search = client.search.create(
      query="AI news headlines",
      max_results=10,
      max_tokens_per_page=512
  )

  for result in detailed_search.results:
      print(f"{result.title}: {result.snippet[:100]}...")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  // Extract more content for comprehensive analysis
  const detailedSearch = await client.search.create({
      query: "artificial intelligence research methodology",
      maxResults: 5,
      maxTokensPerPage: 2048
  });

  // Use default extraction for faster processing
  const quickSearch = await client.search.create({
      query: "AI news headlines",
      maxResults: 3,
      maxTokensPerPage: 512
  });

  for (const result of detailedSearch.results) {
      console.log(`${result.title}: ${result.snippet.substring(0, 100)}...`);
  }
  ```

  ```bash cURL theme={null}
  # Comprehensive content extraction
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer YOUR_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "artificial intelligence research methodology",
      "max_results": 5,
      "max_tokens_per_page": 2048
    }' | jq

  # Lighter content extraction
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer YOUR_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "AI news headlines",
      "max_results": 3,
      "max_tokens_per_page": 512
    }' | jq
  ```
</CodeGroup>

<Info>
  The `max_tokens_per_page` parameter defaults to 1024 tokens. Higher values provide more comprehensive content extraction but may increase processing time. Lower values enable faster processing with more focused content.
</Info>

<Tip>
  Use higher `max_tokens_per_page` values (1500-2048) for research tasks requiring detailed content analysis, and lower values (256-512) for quick information retrieval or when processing large result sets.
</Tip>

## Authentication

Set up your API key as an environment variable:

<Tabs>
  <Tab title="Python/JavaScript">
    ```bash  theme={null}
    export PERPLEXITY_API_KEY="your_api_key_here"
    ```
  </Tab>

  <Tab title="Windows">
    ```bash  theme={null}
    setx PERPLEXITY_API_KEY "your_api_key_here"
    ```
  </Tab>
</Tabs>

Or use a `.env` file:

```bash .env theme={null}
PERPLEXITY_API_KEY=your_api_key_here
```

<CodeGroup>
  ```python Python theme={null}
  import os
  from perplexity import Perplexity

  # Automatically uses PERPLEXITY_API_KEY environment variable
  client = Perplexity()

  # Or specify explicitly
  client = Perplexity(api_key=os.getenv("PERPLEXITY_API_KEY"))
  ```

  ```typescript TypeScript/JavaScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  // Automatically uses PERPLEXITY_API_KEY environment variable
  const client = new Perplexity();

  // Or specify explicitly
  const client = new Perplexity({
      apiKey: process.env.PERPLEXITY_API_KEY
  });
  ```
</CodeGroup>

## Next Steps

<Card title="Best Practices" icon="star" href="/guides/search-best-practices">
  Optimize your queries and implement async patterns
</Card>

## Related Resources

<CardGroup cols={2}>
  <Card title="API Reference" icon="book" href="/api-reference/search-post">
    Complete API documentation
  </Card>

  <Card title="Perplexity SDK" icon="code" href="/guides/perplexity-sdk">
    Type-safe SDK for Python and TypeScript
  </Card>
</CardGroup>

# Search Date and Time Filters

<Note>
  The `search_after_date` and `search_before_date` parameters allow you to restrict search results to a specific publication date range. Only results with publication dates falling between these dates will be returned.

  The `search_recency_filter` parameter provides a convenient way to filter results by predefined time periods (e.g., "day", "week", "month", "year") relative to the current date.
</Note>

<Info>
  Specific date filters must be provided in the "%m/%d/%Y" format (e.g., "3/1/2025"). Recency filters use predefined values like "day", "week", "month", or "year". All filters are optional—you may supply either specific dates or recency filters as needed.
</Info>

## Overview

Date and time filters for the Search API allow you to control which search results are returned by limiting them to specific time periods. There are two types of date and time filters available:

### Publication Date Filters

The `search_after_date` and `search_before_date` parameters filter results based on when content was **originally created or published**. This is useful when you need to:

* Find content published within a specific timeframe
* Exclude outdated or overly recent publications
* Focus on content from a particular publication period

### Search Recency Filter

The `search_recency_filter` parameter provides a simple way to filter results by predefined time periods relative to the current date. This is useful when you need to:

* Find content from the past day, week, month, or year
* Get recent results without specifying exact dates
* Quickly filter for timely information

**Available values:**

* `"day"` - Content from the past 24 hours
* `"week"` - Content from the past 7 days
* `"month"` - Content from the past 30 days
* `"year"` - Content from the past 365 days

**Important:** Publication filters use the original creation/publication date, while recency filters use a relative time period from the current date.

To constrain search results by publication date:

```bash  theme={null}
"search_after_date": "3/1/2025",
"search_before_date": "3/5/2025"
```

To constrain search results by recency:

```bash  theme={null}
"search_recency_filter": "week"
```

These filters will be applied in addition to any other search parameters.

## Examples

**1. Limiting Results by Publication Date Range**

This example limits search results to content published between March 1, 2025, and March 5, 2025.

**Request Example**

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  response = client.search(
      query="latest AI developments",
      max_results=10,
      search_after_date="3/1/2025",
      search_before_date="3/5/2025"
  )

  print(response)
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  const response = await client.search({
    query: "latest AI developments",
    max_results: 10,
    search_after_date: "3/1/2025",
    search_before_date: "3/5/2025"
  });

  console.log(response);
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "latest AI developments",
      "max_results": 10,
      "search_after_date": "3/1/2025",
      "search_before_date": "3/5/2025"
    }' | jq
  ```
</CodeGroup>

**2. Filtering with a Single Publication Date Parameter**

If you only wish to restrict the results to those published on or after a specific date, include just the `search_after_date`:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  response = client.search(
      query="tech news published after March 1, 2025",
      max_results=10,
      search_after_date="3/1/2025"
  )

  print(response)
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  const response = await client.search({
    query: "tech news published after March 1, 2025",
    max_results: 10,
    search_after_date: "3/1/2025"
  });

  console.log(response);
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "tech news published after March 1, 2025",
      "max_results": 10,
      "search_after_date": "3/1/2025"
    }' | jq
  ```
</CodeGroup>

**3. Using Search Recency Filter**

The `search_recency_filter` provides a convenient way to filter results by predefined time periods without specifying exact dates:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  response = client.search(
      query="latest AI developments",
      max_results=10,
      search_recency_filter="week"
  )

  print(response)
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  const response = await client.search({
    query: "latest AI developments",
    max_results: 10,
    search_recency_filter: "week"
  });

  console.log(response);
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "latest AI developments",
      "max_results": 10,
      "search_recency_filter": "week"
    }' | jq
  ```
</CodeGroup>

This example will return only content from the past 7 days, automatically calculated from the current date.

**4. Different Recency Filter Options**

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  # Get content from the past day
  day_response = client.search(
      query="breaking tech news",
      max_results=5,
      search_recency_filter="day"
  )

  # Get content from the past month
  month_response = client.search(
      query="AI research developments",
      max_results=10,
      search_recency_filter="month"
  )

  # Get content from the past year
  year_response = client.search(
      query="major tech trends",
      max_results=15,
      search_recency_filter="year"
  )
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  // Get content from the past day
  const dayResponse = await client.search({
    query: "breaking tech news",
    max_results: 5,
    search_recency_filter: "day"
  });

  // Get content from the past month
  const monthResponse = await client.search({
    query: "AI research developments",
    max_results: 10,
    search_recency_filter: "month"
  });

  // Get content from the past year
  const yearResponse = await client.search({
    query: "major tech trends",
    max_results: 15,
    search_recency_filter: "year"
  });
  ```

  ```bash cURL theme={null}
  # Get content from the past day
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "breaking tech news",
      "max_results": 5,
      "search_recency_filter": "day"
    }' | jq

  # Get content from the past month
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "AI research developments",
      "max_results": 10,
      "search_recency_filter": "month"
    }' | jq
  ```
</CodeGroup>

## Parameter Reference

### `search_after_date`

* **Type**: String
* **Format**: "%m/%d/%Y" (e.g., "3/1/2025")
* **Description**: Filters search results to only include content published after this date
* **Optional**: Yes
* **Example**: `"search_after_date": "1/1/2025"`

### `search_before_date`

* **Type**: String
* **Format**: "%m/%d/%Y" (e.g., "3/1/2025")
* **Description**: Filters search results to only include content published before this date
* **Optional**: Yes
* **Example**: `"search_before_date": "12/31/2025"`

### `search_recency_filter`

* **Type**: String
* **Allowed Values**: "day", "week", "month", "year"
* **Description**: Filters search results to content from the specified time period relative to the current date
* **Optional**: Yes
* **Example**: `"search_recency_filter": "week"`

## Best Practices

**Date Format**

* Strict Format: Dates must match the "%m/%d/%Y" format exactly. For example, "3/1/2025" or "03/01/2025" is acceptable.
* Consistency: Use one or both date filters consistently based on your search needs. Combining both provides a clear range.

**Filter Selection**

* Choose the Right Filter Type: Use publication date filters (`search_after_date`/`search_before_date`) when you care about when content was originally created. Use recency filters (`search_recency_filter`) for quick, relative time filtering.
* Recency vs. Exact Dates: Use `search_recency_filter` for convenience when you want recent content (e.g., "past week"). Use specific date filters when you need precise control over the time range.
* Filter Limitations: Note that `search_recency_filter` cannot be combined with specific date filters (`search_after_date`/`search_before_date`).

**Client-Side Validation**

* Regex Check: Validate date strings on the client side using a regex such as:

<CodeGroup>
  ```bash  theme={null}
  date_regex='^(0?[1-9]|1[0-2])/(0?[1-9]|[12][0-9]|3[01])/[0-9]{4}$'
  ```

  ```python  theme={null}
  date_regex = r'^(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/\d{4}$'
  ```
</CodeGroup>

This ensures that dates conform to the required format before sending the request.

**Performance Considerations**

* Narrowing the Search: Applying date range filters typically reduces the number of results, which may improve response times and result relevance.
* Avoid Over-Restriction: Ensure that the date range is neither too narrow (limiting useful results) nor too broad (defeating the purpose of the filter).

## Advanced Usage Patterns

**Finding Breaking News**

Use the `search_recency_filter` with "day" to find the most recent breaking news:

```python  theme={null}
response = client.search(
    query="breaking news technology",
    max_results=5,
    search_recency_filter="day"
)
```

**Historical Research**

Use specific date ranges to research historical events or trends:

```python  theme={null}
response = client.search(
    query="AI developments",
    max_results=20,
    search_after_date="1/1/2023",
    search_before_date="12/31/2023"
)
```

**Trend Analysis**

Compare different time periods by making multiple searches:

```python  theme={null}
# Recent trends
recent = client.search(
    query="machine learning trends",
    search_recency_filter="month"
)

# Older trends for comparison
older = client.search(
    query="machine learning trends",
    search_after_date="1/1/2023",
    search_before_date="1/31/2023"
)
```

## Error Handling

When using date filters, ensure proper error handling for invalid date formats:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity
  import re

  def validate_date_format(date_string):
      pattern = r'^(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/\d{4}$'
      return bool(re.match(pattern, date_string))

  def search_with_date_filter(query, after_date=None, before_date=None):
      client = Perplexity()
      
      # Validate date formats
      if after_date and not validate_date_format(after_date):
          raise ValueError(f"Invalid date format: {after_date}")
      if before_date and not validate_date_format(before_date):
          raise ValueError(f"Invalid date format: {before_date}")
      
      try:
          response = client.search(
              query=query,
              search_after_date=after_date,
              search_before_date=before_date
          )
          return response
      except Exception as e:
          print(f"Search failed: {e}")
          return None
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  function validateDateFormat(dateString: string): boolean {
    const pattern = /^(0?[1-9]|1[0-2])\/(0?[1-9]|[12]\d|3[01])\/\d{4}$/;
    return pattern.test(dateString);
  }

  async function searchWithDateFilter(
    query: string, 
    afterDate?: string, 
    beforeDate?: string
  ) {
    const client = new Perplexity();
    
    // Validate date formats
    if (afterDate && !validateDateFormat(afterDate)) {
      throw new Error(`Invalid date format: ${afterDate}`);
    }
    if (beforeDate && !validateDateFormat(beforeDate)) {
      throw new Error(`Invalid date format: ${beforeDate}`);
    }
    
    try {
      const response = await client.search({
        query,
        search_after_date: afterDate,
        search_before_date: beforeDate
      });
      return response;
    } catch (error) {
      console.error('Search failed:', error);
      return null;
    }
  }
  ```
</CodeGroup>

# Search Domain Filter

<Note>
  The `search_domain_filter` parameter allows you to limit search results to specific domains or exclude certain domains from search results. This supports domain-level filtering for precise content control.
</Note>

<Warning>
  You can add a maximum of 20 domains to the `search_domain_filter` list. The filter works in either allowlist mode (include only) or denylist mode (exclude), but not both simultaneously.
</Warning>

<Info>
  Domain filters allow you to specify which domains to include or exclude in search results. You can filter by specific domains, top-level domains (TLDs), or domain parts. You can specify up to 20 domains per request. Domains should be provided without the protocol (e.g., "nature.com" not "[https://nature.com](https://nature.com)").
</Info>

## Overview

The domain filter for the Search API allows you to control which sources appear in your search results by limiting them to specific domains or excluding certain domains. This is particularly useful when you need to:

* Focus research on authoritative or trusted sources
* Filter out specific domains from search results
* Search within specific publication networks or organizations
* Build domain-specific search applications
* Conduct competitive research within specific industry domains

The `search_domain_filter` parameter accepts an array of domain strings. The filter operates in two modes:

* **Allowlist mode**: Include only the specified domains (no `-` prefix)
* **Denylist mode**: Exclude the specified domains (use `-` prefix)

```bash  theme={null}
# Allowlist: Only search these domains
"search_domain_filter": ["nature.com", "science.org", "cell.com"]

# Denylist: Exclude these domains
"search_domain_filter": ["-reddit.com", "-pinterest.com", "-quora.com"]
```

## Filtering Capabilities

Domain filters support flexible matching across different domain components:

### Root Domain Filtering

Specify a root domain to match all content from that domain and its subdomains:

```bash  theme={null}
"search_domain_filter": ["wikipedia.org"]
```

This will match:

* `en.wikipedia.org`
* `fr.wikipedia.org`
* `de.wikipedia.org`
* Any other Wikipedia language subdomain

### Top-Level Domain (TLD) Filtering

Filter by top-level domain to target specific categories of sites:

```bash  theme={null}
"search_domain_filter": [".gov"]
```

This will match all government domains:

* `nasa.gov`
* `cdc.gov`
* `irs.gov`
* Any other `.gov` domain

<Tip>
  TLD filtering is particularly useful for targeting specific types of organizations, such as `.gov` for government sites, `.edu` for educational institutions, or country-specific TLDs like `.uk` or `.ca`.
</Tip>

### Domain Part Filtering

Any part of a domain can be used as a filter. The system will match domains containing that component.

## Examples

**1. Allowlist Specific Domains**

This example limits search results to authoritative academic publishers:

**Request Example**

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  response = client.search.create(
      query="climate change research",
      max_results=10,
      search_domain_filter=[
          "nature.com",
          "science.org",
          "cell.com"
      ]
  )

  for result in response.results:
      print(f"{result.title}: {result.url}")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  const response = await client.search.create({
    query: "climate change research",
    maxResults: 10,
    searchDomainFilter: [
      "nature.com",
      "science.org",
      "cell.com"
    ]
  });

  for (const result of response.results) {
    console.log(`${result.title}: ${result.url}`);
  }
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "climate change research",
      "max_results": 10,
      "search_domain_filter": [
        "nature.com",
        "science.org",
        "cell.com"
      ]
    }' | jq
  ```
</CodeGroup>

**2. Tech News Sources**

Search across major technology news websites:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  response = client.search.create(
      query="latest tech trends 2025",
      max_results=10,
      search_domain_filter=[
          "techcrunch.com",
          "theverge.com",
          "arstechnica.com",
          "wired.com"
      ]
  )

  for result in response.results:
      print(f"{result.title}")
      print(f"Source: {result.url}")
      print(f"Date: {result.date}")
      print("---")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  const response = await client.search.create({
    query: "latest tech trends 2025",
    maxResults: 10,
    searchDomainFilter: [
      "techcrunch.com",
      "theverge.com",
      "arstechnica.com",
      "wired.com"
    ]
  });

  for (const result of response.results) {
    console.log(`${result.title}`);
    console.log(`Source: ${result.url}`);
    console.log(`Date: ${result.date}`);
    console.log("---");
  }
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "latest tech trends 2025",
      "max_results": 10,
      "search_domain_filter": [
        "techcrunch.com",
        "theverge.com",
        "arstechnica.com",
        "wired.com"
      ]
    }' | jq
  ```
</CodeGroup>

**3. Government and Educational Sources (TLD Filtering)**

Use top-level domain filtering to search across all government or educational institutions:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  # Search all .gov and .edu domains
  response = client.search.create(
      query="climate change policy research",
      max_results=15,
      search_domain_filter=[
          ".gov",
          ".edu"
      ]
  )

  for result in response.results:
      print(f"{result.title}")
      print(f"Source: {result.url}")
      print("---")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  // Search all .gov and .edu domains
  const response = await client.search.create({
    query: "climate change policy research",
    maxResults: 15,
    searchDomainFilter: [
      ".gov",
      ".edu"
    ]
  });

  for (const result of response.results) {
    console.log(`${result.title}`);
    console.log(`Source: ${result.url}`);
    console.log("---");
  }
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "climate change policy research",
      "max_results": 15,
      "search_domain_filter": [
        ".gov",
        ".edu"
      ]
    }' | jq
  ```
</CodeGroup>

**4. Wikipedia Across Languages (Subdomain Matching)**

Search across all Wikipedia language editions by specifying the root domain:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  # Matches en.wikipedia.org, fr.wikipedia.org, de.wikipedia.org, etc.
  response = client.search.create(
      query="quantum mechanics",
      max_results=10,
      search_domain_filter=["wikipedia.org"]
  )

  for result in response.results:
      print(f"{result.title}")
      print(f"URL: {result.url}")
      print("---")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  // Matches en.wikipedia.org, fr.wikipedia.org, de.wikipedia.org, etc.
  const response = await client.search.create({
    query: "quantum mechanics",
    maxResults: 10,
    searchDomainFilter: ["wikipedia.org"]
  });

  for (const result of response.results) {
    console.log(`${result.title}`);
    console.log(`URL: ${result.url}`);
    console.log("---");
  }
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "quantum mechanics",
      "max_results": 10,
      "search_domain_filter": ["wikipedia.org"]
    }' | jq
  ```
</CodeGroup>

**5. Denylist Specific Domains**

This example shows how to exclude specific domains from search results by prefixing the domain name with a minus sign (`-`):

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  # Exclude social media and Q&A sites from search results
  response = client.search.create(
      query="latest advancements in renewable energy",
      max_results=10,
      search_domain_filter=[
          "-pinterest.com",
          "-reddit.com",
          "-quora.com"
      ]
  )

  for result in response.results:
      print(f"{result.title}: {result.url}")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  // Exclude social media and Q&A sites from search results
  const response = await client.search.create({
    query: "latest advancements in renewable energy",
    maxResults: 10,
    searchDomainFilter: [
      "-pinterest.com",
      "-reddit.com",
      "-quora.com"
    ]
  });

  for (const result of response.results) {
    console.log(`${result.title}: ${result.url}`);
  }
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "latest advancements in renewable energy",
      "max_results": 10,
      "search_domain_filter": [
        "-pinterest.com",
        "-reddit.com",
        "-quora.com"
      ]
    }' | jq
  ```
</CodeGroup>

**6. Combining Domain Filter with Other Filters**

Domain filters work seamlessly with other search parameters for precise control:

<CodeGroup>
  ```python Python theme={null}
  from perplexity import Perplexity

  client = Perplexity()

  # Combine domain filter with date and language filters
  response = client.search.create(
      query="quantum computing breakthroughs",
      max_results=20,
      search_domain_filter=[
          "nature.com",
          "science.org",
          "arxiv.org"
      ],
      search_recency_filter="month",
      search_language_filter=["en"]
  )

  for result in response.results:
      print(f"{result.title}")
      print(f"URL: {result.url}")
      print(f"Date: {result.date}")
      print("---")
  ```

  ```typescript TypeScript theme={null}
  import Perplexity from '@perplexity-ai/perplexity_ai';

  const client = new Perplexity();

  // Combine domain filter with date and language filters
  const response = await client.search.create({
    query: "quantum computing breakthroughs",
    maxResults: 20,
    searchDomainFilter: [
      "nature.com",
      "science.org",
      "arxiv.org"
    ],
    searchRecencyFilter: "month",
    searchLanguageFilter: ["en"]
  });

  for (const result of response.results) {
    console.log(`${result.title}`);
    console.log(`URL: ${result.url}`);
    console.log(`Date: ${result.date}`);
    console.log("---");
  }
  ```

  ```bash cURL theme={null}
  curl -X POST 'https://api.perplexity.ai/search' \
    -H 'Authorization: Bearer $PERPLEXITY_API_KEY' \
    -H 'Content-Type: application/json' \
    -d '{
      "query": "quantum computing breakthroughs",
      "max_results": 20,
      "search_domain_filter": [
        "nature.com",
        "science.org",
        "arxiv.org"
      ],
      "search_recency_filter": "month",
      "search_language_filter": ["en"]
    }' | jq
  ```
</CodeGroup>

## Parameter Reference

### `search_domain_filter`

* **Type**: Array of strings
* **Format**:
  * Domain names without protocol (e.g., "example.com")
  * Prefix with `-` for denylisting (e.g., "-reddit.com")
* **Description**: Filters search results to include or exclude content from specified domains
* **Optional**: Yes
* **Maximum**: 20 domains per request
* **Modes**:
  * Allowlist mode: Include only specified domains (no `-` prefix)
  * Denylist mode: Exclude specified domains (use `-` prefix)
* **Example**:
  * Allowlist: `"search_domain_filter": ["nature.com", "science.org"]`
  * Denylist: `"search_domain_filter": ["-reddit.com", "-pinterest.com"]`

## Domain Format Guidelines

**Correct Domain Formats:**

* `"nature.com"` - Root domain (matches nature.com and all subdomains)
* `"blog.example.com"` - Specific subdomain
* `"arxiv.org"` - Root domain (matches all subdomains)
* `".gov"` - Top-level domain (matches all .gov sites)
* `".edu"` - Top-level domain (matches all .edu sites)
* `".uk"` - Country-code TLD (matches all .uk sites)
* `"wikipedia.org"` - Matches en.wikipedia.org, fr.wikipedia.org, etc.
* `"-reddit.com"` - Exclude entire domain and all subdomains
* `"-pinterest.com"` - Exclude domain
* `"-.gov"` - Exclude all .gov domains

**Incorrect Domain Formats:**

* L `"https://nature.com"` - Don't include protocol
* L `"nature.com/"` - Don't include trailing slash
* L `"nature.com/articles"` - Don't include path (path filtering coming soon)
* L `"www.nature.com"` - Avoid www prefix (use root domain)

## Best Practices

### Domain Selection Strategy

* **Use Root Domains for Broad Coverage**: Specify root domains (e.g., "wikipedia.org") to match all subdomains automatically, including different language versions and regional sites.
* **Use TLDs for Categorical Filtering**: Target specific organization types with TLD filters like `.gov` for government, `.edu` for education, or `.org` for non-profits.
* **Be Specific When Needed**: Choose specific domains that are directly relevant to your search query to ensure high-quality results.
* **Quality Over Quantity**: Using fewer, highly relevant domains often produces better results than maximizing the 20-domain limit.
* **Consider Domain Authority**: Prioritize authoritative sources in your field for more reliable information.
* **Choose Your Mode**: Use either allowlist mode (include only) OR denylist mode (exclude), but not both in the same request. Denylisting is useful when you want broad search coverage but need to exclude specific low-quality or irrelevant sources.

### Locale and Regional Targeting

While domain filters don't directly filter by user location, you can target specific locales using:

* **Country-code TLDs**: Use filters like `.uk`, `.ca`, `.de`, `.jp` to target country-specific domains
* **Subdomain matching**: Specify regional subdomains when available (e.g., "uk.domain.com")
* **Combined approach**: Mix country TLDs with specific trusted domains for precise regional filtering

<Tip>
  For international research, combine TLD filtering with the `search_language_filter` parameter to refine results by both location and language.
</Tip>

### Domain Selection

* **Use Trusted Sources**: Select a specific set of trusted sources you want to search within (e.g., academic research, official documentation).
* **Leverage TLD Filtering**: When researching topics that span many sites of the same type, use TLD filters to cast a wider net (e.g., `.gov` for policy research).
* **Focus on Quality**: Choose authoritative domains that consistently provide reliable information relevant to your queries.

### Client-Side Validation

Validate domain formats on the client side before sending requests:

<CodeGroup>
  ```python Python theme={null}
  import re

  def validate_domain(domain):
      """Validate domain format including TLD filters."""
      # TLD filter (e.g., .gov, .edu)
      if domain.startswith('.'):
          tld_pattern = r'^\.[a-zA-Z]{2,}$'
          return bool(re.match(tld_pattern, domain))
      
      # Standard domain validation pattern
      pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'
      return bool(re.match(pattern, domain))

  def validate_domain_filter(domains):
      """Validate domain filter array."""
      if len(domains) > 20:
          raise ValueError("Maximum 20 domains allowed")

      for domain in domains:
          if not validate_domain(domain):
              raise ValueError(f"Invalid domain format: {domain}")

      return True

  # Usage examples
  try:
      # Mix of regular domains and TLD filters
      domains = ["nature.com", "science.org", ".gov", ".edu"]
      validate_domain_filter(domains)

      response = client.search.create(
          query="research topic",
          search_domain_filter=domains
      )
  except ValueError as e:
      print(f"Validation error: {e}")
  ```

  ```typescript TypeScript theme={null}
  function validateDomain(domain: string): boolean {
    // TLD filter (e.g., .gov, .edu)
    if (domain.startsWith('.')) {
      const tldPattern = /^\.[a-zA-Z]{2,}$/;
      return tldPattern.test(domain);
    }
    
    // Standard domain validation pattern
    const pattern = /^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$/;
    return pattern.test(domain);
  }

  function validateDomainFilter(domains: string[]): void {
    if (domains.length > 20) {
      throw new Error("Maximum 20 domains allowed");
    }

    for (const domain of domains) {
      if (!validateDomain(domain)) {
        throw new Error(`Invalid domain format: ${domain}`);
      }
    }
  }

  // Usage examples
  try {
    // Mix of regular domains and TLD filters
    const domains = ["nature.com", "science.org", ".gov", ".edu"];
    validateDomainFilter(domains);

    const response = await client.search.create({
      query: "research topic",
      searchDomainFilter: domains
    });
  } catch (error) {
    console.error("Validation error:", error.message);
  }
  ```
</CodeGroup>

### Performance Considerations

* **Result Availability**: Narrowing to specific domains may reduce the number of available results. Be prepared to handle cases where fewer results are returned than requested.
* **Domain Coverage**: Ensure the domains you specify actually contain content relevant to your query. Overly restrictive filters may return zero results.
* **Combination Effects**: Domain filters combined with other restrictive filters (date, language) can significantly reduce result counts.

<Tip>
  For best results, combine domain filtering with other filters like `search_recency_filter` or `search_language_filter` to narrow down your search to highly relevant, timely content from your target sources. Use TLD filters like `.gov` or `.edu` when you need broad coverage across an entire category of authoritative sites.
</Tip>

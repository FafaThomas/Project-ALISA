# Project ALISA

> **A personal intelligence-augmentation system built around human-directed, multi-agent orchestration.**

**ALISA** stands for **Advanced Learning & Intelligent Systems Architecture**. It is a modular AI ecosystem designed to extend its creator's capabilities across technical, scientific, creative, social, financial, defensive, practical, linguistic, and cultural domains.

ALISA is not intended to replace its user or independently determine the user's goals. The user acts as its central orchestrator: defining objectives, providing direction, evaluating results, and deciding what should happen next. ALISA handles delegated work by coordinating specialized agents and the knowledge, tools, and workflows they need.

Rather than relying on one general-purpose agent to do everything, ALISA organizes expertise into **eight factions**. Each faction represents a broad domain of capability and can contain multiple specialist agents, tools, services, and knowledge bases.

---

## Core Philosophy

> **ALISA extends what its user can do. The user sets the direction; ALISA coordinates the work.**

The system is built around a few principles:

- **Human-directed:** The user defines the goal, constraints, and desired outcome.
- **Specialized:** Agents have explicit responsibilities, instructions, tools, and knowledge sources.
- **Adaptive collaboration:** A task may involve one faction or several, depending on what is most relevant.
- **User-steered iteration:** The user can redirect the work, introduce new requirements, or request additional perspectives at any time.
- **Constructive disagreement:** Relevant factions can challenge assumptions and assess a problem using different disciplinary standards.
- **Modular growth:** New specialists and capabilities can be added without requiring every part of ALISA to be redesigned.
- **Evidence and provenance:** Where appropriate, outputs should distinguish evidence, assumptions, uncertainty, and conclusions.
- **Proportionate orchestration:** Simple tasks should not require unnecessary agents, debate, or orchestration layers.

ALISA does not need to anticipate every possible consideration in a single pass. It should make a reasonable initial selection, produce useful work, and remain easy for the user to guide.

---

# The Eight Factions

The factions are ALISA's broad domains of expertise. They are not eight agents that must participate in every task, nor are they a rigid hierarchy. Each faction can operate independently or collaborate with others when a task crosses domain boundaries.

## 1. The Forge — Technology and Physical Engineering

**Domain:** Engineering, modeling, coding, networking, hacking, robotics, and technology or physical-engineering disciplines.

The Forge designs, builds, analyzes, repairs, and secures technical systems. It may contain specialists for software development, codebase understanding, systems architecture, electronics, embedded systems, networking, robotics, cybersecurity, automation, and other engineering disciplines.

Potential responsibilities include:

- Software development, debugging, testing, and code review
- System architecture, modeling, simulation, and optimization
- Networking, infrastructure, and distributed systems
- Electronics, embedded software, robotics, and physical-system integration
- Authorized security research and defensive cybersecurity
- Technical tooling and infrastructure that support ALISA itself

## 2. The Pentagram — Research and Science

**Domain:** Chemistry, physics, mathematics, biology, medicine, and related scientific disciplines.

The Pentagram investigates questions, evaluates evidence, develops scientific explanations, and applies rigorous domain-specific reasoning. Its specialist agents may maintain focused knowledge bases and retrieval pipelines for particular scientific fields.

Potential responsibilities include:

- Scientific research and evidence synthesis
- Mathematical reasoning, statistics, and modeling
- Chemistry, physics, and biological sciences
- Medical and pharmacological knowledge support
- Research design, experimental feasibility, and interpretation
- Retrieval-augmented access to curated scientific references and notes

Medical or pharmacological capabilities are intended to support the user, who remains the clinician when acting in that role. Clinical outputs should preserve source provenance, make uncertainty visible, and support verification against current authoritative references.

## 3. The Canvas — Art and Creative Production

**Domain:** Painting, videography, sketching, writing, music, and related creative disciplines.

The Canvas develops creative work across visual, literary, cinematic, and auditory media. It can contain specialist agents for different creative roles, styles, and production stages.

Potential responsibilities include:

- Painting, illustration, sketching, and visual development
- Videography, cinematography, editing, and storyboarding
- Writing, literature, scripts, and narrative design
- Music composition, arrangement, and sound design
- Creative direction, critique, revision, and production planning

## 4. The Quill — Social Sciences and People

**Domain:** Psychology, ethics, law, sociology, anthropology, philosophy, political science, and the study of people and societies.

The Quill examines human behavior, social systems, values, institutions, and competing interpretations. It can contribute empirical, philosophical, ethical, legal, and social perspectives without treating them as interchangeable.

Potential responsibilities include:

- Psychology, cognition, motivation, and behavior
- Ethics, moral philosophy, and structured ethical analysis
- Law, jurisprudence, rights, and legal systems
- Sociology, anthropology, and cultural or institutional analysis
- Political science, governance, power, and public policy
- Analysis of prosocial and harmful behavior, including manipulation and conflict

The Quill can challenge the implications of a proposal even when another faction considers it technically feasible. Its analyses should distinguish empirical findings, ethical arguments, and legal considerations.

## 5. The Gauntlet — Wealth and Financial Strategy

**Domain:** Wealth generation, finance, investing, financial management, and resource allocation.

The Gauntlet helps evaluate financial choices, build and manage resources, and compare opportunities against costs, uncertainty, and risk.

Potential responsibilities include:

- Personal finance, budgeting, cash flow, and debt management
- Investing, portfolio analysis, and capital allocation
- Financial modeling, forecasting, and scenario analysis
- Business strategy, entrepreneurship, monetization, and revenue generation
- Risk management, liquidity, inflation, and opportunity cost
- Cost estimation and affordability analysis for projects

Financial recommendations should make assumptions, risks, and uncertainty clear rather than focusing only on potential returns.

## 6. The Sword — Defense and Protective Security

**Domain:** Martial arts, fitness, weapon knowledge, the art of war, and protective security systems.

The Sword focuses on preparedness, defensive disciplines, strategic security, and the protection of people and property.

Potential responsibilities include:

- Martial arts, self-defense, and situational awareness
- Physical conditioning, strength, endurance, and readiness
- Responsible weapon knowledge and safe handling
- Military history, strategic theory, logistics, and defensive planning
- Security monitoring, access control, sensors, and incident response
- Future integration with authorized surveillance and defensive systems, including drones

Future physical-system integrations require explicit permissions, bounded autonomy, privacy protections, appropriate human oversight, and fail-safe behavior. Planned integrations are not necessarily implemented capabilities.

## 7. The Knife — Food, Agriculture, Hunting, and Cooking

**Domain:** Food, agriculture, hunting, cooking, and the sciences applied to food.

The Knife covers food from production and procurement through preparation, preservation, and consumption.

Potential responsibilities include:

- Cooking, culinary techniques, recipes, and flavor development
- Agriculture, cultivation, horticulture, and food production
- Food chemistry, food microbiology, and cooking science
- Lawful and sustainable hunting and wild-food handling
- Food preservation, storage, and food safety
- Nutrition, ingredient analysis, meal planning, and food economics

## 8. The Corpus — Languages and Cultures

**Domain:** Languages, linguistics, translation, interpretation, and cultures.

The Corpus helps ALISA understand and communicate across languages and cultural contexts. It treats language as more than word substitution: meaning, register, idiom, history, and context matter.

Potential responsibilities include:

- Linguistics, grammar, syntax, semantics, and pragmatics
- Translation, interpretation, and localization
- Language learning, pronunciation, and writing
- Cultural practices, symbolism, etiquette, and communication norms
- Etymology, historical linguistics, writing systems, and language preservation
- Cross-cultural communication and multilingual research

Cultural analysis should account for regional, generational, social, and individual variation rather than treating any culture as uniform.

---

# Orchestration Model

ALISA uses hierarchical orchestration, but orchestration exists to make work easier to direct—not to create unnecessary layers for their own sake.

```text
                         USER
                 Human Orchestrator
                           |
                           v
                 ALISA Central Orchestrator
                           |
              Select the most relevant faction(s)
                           |
           +---------------+----------------+
           |               |                |
           v               v                v
       The Forge      The Pentagram      The Quill
           |               |                |
           v               v                v
      Specialists     Specialists       Specialists
      Tools / RAG     Tools / RAG       Tools / RAG
           |               |                |
           +---------------+----------------+
                           |
                           v
                 Consolidated Results
                           |
                           v
                    User Direction
```

This diagram illustrates a possible task, not a fixed list of participating factions. The central orchestrator may select any relevant subset of the eight factions.

## The User Directs the Work

The user is ALISA's ultimate director. The central orchestrator interprets the request, chooses a reasonable initial set of participants, and coordinates their work. The user can add requirements, ask for another faction's perspective, change priorities, or redirect the task at any point.

For example, a user-directed experiment assessment may evolve in stages:

1. **Scientific feasibility — The Pentagram:** Is the proposed experiment scientifically plausible, and what evidence or methodology is relevant?
2. **Ethical implications — The Quill:** What ethical, social, or legal concerns need to be addressed?
3. **Implementation — The Forge:** What technical approach, equipment, and tools would be required?
4. **Affordability — The Gauntlet:** What would it cost, can it be funded, and what alternatives exist?

The user may request each perspective as the work progresses. ALISA does not need to involve every faction at the beginning.

## Dynamic Cross-Faction Collaboration

A faction is selected when its expertise is relevant to the current task. If an omitted perspective becomes important, the user can request it, or the orchestration workflow can bring it in when the need becomes apparent.

Factions can:

- Work independently on separate parts of a task
- Share task-relevant findings with other participating factions
- Challenge assumptions or conclusions from another discipline
- Revise an assessment in response to evidence or a strong counterargument
- Return unresolved disagreements and trade-offs to the user

Not every disagreement needs to end in consensus. ALISA should preserve meaningful differences in evidence, reasoning, or priorities rather than hiding them behind an artificial agreement. Serious safety, legal, or ethical constraints cannot be overridden merely by a majority vote or by a finding of technical feasibility.

## Specialist Agents Within Factions

A faction can contain multiple specialist agents. A specialist may have its own:

- Defined responsibility and instructions
- Tools and execution permissions
- Input and output contracts
- RAG collection or other knowledge sources
- Evaluation criteria and tests
- Internal workflow or sub-orchestration, where justified

For example, The Pentagram may contain separate pharmacology, biology, mathematics, and medical-science specialists. These are examples of possible specialists, not a claim that all have already been implemented.

## Keep Orchestration Proportionate

ALISA should use the simplest workflow that can perform the task well.

- A straightforward task may need only one specialist.
- A multidisciplinary task may require multiple agents or factions.
- A task may expand as the user introduces new questions or constraints.
- Additional debate or orchestration should be used when it improves the result, not by default.

The goal is useful, controllable collaboration—not the largest possible agent graph.

---

# Knowledge and Memory

ALISA can use retrieval-augmented generation (RAG), memory systems, and other knowledge sources to give agents relevant context.

The intended approach is to keep knowledge organized by domain and purpose while allowing controlled cross-faction collaboration.

Possible capabilities include:

- Domain-specific RAG collections
- Ingestion pipelines for different kinds of knowledge and files
- Source metadata and provenance
- Short-term task context and long-term memory
- Retrieval tailored to a specialist's responsibilities
- Cross-faction retrieval when a task requires another domain's knowledge

A faction's knowledge base should not be assumed to be complete or automatically authoritative. Retrieval quality, source quality, freshness, and correct interpretation must be evaluated. Where accuracy matters, agents should identify supporting sources and communicate missing or conflicting evidence.

---

# Design Principles

## Domain Specialization

Agents should have clear responsibilities and boundaries. Specialization makes it easier to develop, test, evaluate, and improve individual capabilities.

## Modular Architecture

Factions, agents, tools, and services should expose well-defined interfaces. Adding a capability should avoid unnecessary changes to unrelated parts of ALISA.

## Human Direction and Control

ALISA should remain steerable by its user. The user defines objectives and can redirect work, while consequential actions should respect their configured permissions and safeguards.

## Independent Analysis and Constructive Challenge

When a task calls for multiple perspectives, agents should be able to analyze their responsibilities and challenge claims using relevant evidence and standards. Disagreement should be substantive and traceable.

## Demand-Driven Development

Capabilities should be built as useful needs arise rather than fully implementing every possible agent upfront.

## Explicit Contracts

Agents and services should exchange structured inputs and outputs where appropriate. Contracts should make responsibilities, required information, results, and failures easier to inspect.

## Evaluation and Reliability

Agent behavior, retrieval quality, tool execution, and multi-agent workflows should be tested. A well-structured response is not by itself proof that the result is correct.

## Safety and Permissions

Tool access and execution authority should match the task and its consequences. Actions involving clinical decisions, money, private data, physical systems, or other consequential outcomes need appropriate validation and safeguards.

---

# Why Custom Orchestration?

ALISA uses a custom orchestration architecture rather than depending entirely on a single external agent framework.

The intent is to retain control over how tasks are routed, how specialist agents are composed, how faction-level workflows operate, and how tools and knowledge are integrated.

Potential benefits include:

- Control over execution and delegation behavior
- Support for nested or faction-specific orchestration
- Workflows tailored to individual domains
- Explicit interfaces between agents and services
- The ability to use external orchestration frameworks selectively when they provide a clear advantage

Custom orchestration is not automatically better in every case. It introduces maintenance and testing responsibilities, so each custom component should justify its complexity through control, performance, flexibility, or a concrete requirement.

---

# Technology Stack

The following technologies appeared in the project's earlier technical direction. This list is not a guarantee that every component is currently implemented or remains part of the active stack.

## Core and Backend

- Python
- FastAPI
- PostgreSQL
- Docker

## AI and Model Integration

- OpenAI API
- Ollama
- Hugging Face
- Whisper
- Sentence Transformers

## Retrieval and Knowledge

- Custom RAG pipelines
- PostgreSQL with pgvector (previously considered)

## Infrastructure and Messaging

- Docker Compose
- Redis
- RabbitMQ or another event queue (previously planned)

Actual implementation status should be verified against the current codebase and configuration.

---

# Development Direction

ALISA is developed as an evolving ecosystem rather than a single monolithic application.

Development should prioritize the capabilities needed for useful end-to-end workflows, including:

- Central orchestration and faction routing
- Specialist-agent contracts and registries
- Domain-specific knowledge ingestion and retrieval
- Cross-faction task handoffs
- Traceable results and structured disagreement
- Tool permissions and execution controls
- Evaluation, testing, and observability

The exact implementation order should follow the current state of the repository and the highest-value next capability.

---

# Long-Term Vision

The long-term goal is to build a personal intelligence-augmentation system that can coordinate a growing ecosystem of specialized agents across the user's domains of interest.

ALISA should be capable of carrying out delegated work, retrieving relevant knowledge, coordinating multidisciplinary efforts, challenging assumptions, and adapting as the user directs the process.

The ambition is not to make one model know everything. It is to build a system in which specialized capabilities can be assembled when needed, while the human user remains the source of direction and the final authority over their own goals.

> **ALISA extends the user's capabilities. The user sets the direction; the factions bring their expertise; the system coordinates the work.**

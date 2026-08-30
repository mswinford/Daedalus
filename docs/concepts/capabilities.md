# Capability Manifest - Development Phases

|Phase|Focus|Outcome|
|---|---|---|
|**0**|Core contract|Define what a capability is|
|**1**|Identity & interface|Capabilities can be discovered and invoked|
|**2**|Runtime & dependencies|Platform knows how to run them|
|**3**|Access & security|Platform knows what they can access|
|**4**|Quality & governance|Platform knows whether they're safe/good|
|**5**|Agent-native capabilities|Agents can discover and consume them|

---

## Phase 0 - Core Capability Contract

**Goal:** Define the minimum viable capability.

```yaml
kind: Capability

metadata:
  name: invoice-analyzer
  version: 1.0.0
  description: Analyze invoices

ownership:
  team: Finance AI

interface:
  type: mcp
```

### Build

- Capability schema
- Name / ID conventions
- Semantic versioning
- Ownership
- Description / metadata
- Basic validation

### Result

You have a standardized way to say:

> "This is an AI capability."

---

## Phase 1 - Interface & Invocation

**Goal:** Make a capability understandable and callable.

```yaml
interface:
  type: mcp

  tools:
    - name: analyze_invoice

      input:
        type: object

      output:
        type: object
```

### Build

- MCP support
- REST/API support where needed
- Input/output schemas
- Tool definitions
- Invocation metadata
- Interface validation

### Result

The platform can answer:

> **What does this capability expose and how do I call it?**

---

## Phase 2 - Runtime & Dependencies

**Goal:** Make capabilities portable and executable.

```yaml
runtime:
  type: container

  image:
    repository: registry.company.com/ai/invoice-analyzer
    tag: 1.0.0

dependencies:

  capabilities:
    - pdf.extract: "^2.0"

  packages:
    python:
      - pandas
      - numpy
```

### Build

- Runtime specification
- Container support
- Dependency specification
- Dependency resolution
- Resource requirements
- Environment configuration

### Result

The platform can answer:

> **What does this capability need in order to run?**

And eventually:

> **Provision it for me.**

---

## Phase 3 - Access & Security

**Goal:** Make capabilities enterprise-safe.

```yaml
credentials:

  - name: salesforce
    type: oauth
    scopes:
      - customer.read

permissions:

  data:
    - customer.profile.read

  services:
    - salesforce.customer.read

data:

  input:
    classification: confidential
```

### Build

- Permission declarations
- Credential requirements
- Secret references
- Data classification
- Identity requirements
- Human approval requirements
- Security validation

### Result

The platform can answer:

> **What does this capability need access to, and is this caller allowed to use it?**

---

## Phase 4 - Quality & Governance

**Goal:** Make capabilities production-grade.

```yaml
evaluation:

  suite: invoice-analysis-v2

  requirements:
    accuracy:
      minimum: 0.90

    latency:
      p95:
        maximum: 5s

governance:

  humanApproval:
    required: false

lifecycle:

  stage: production
  status: active
```

### Build

- Evaluation metadata
- Automated quality gates
- Security approval
- Lifecycle management
- Deprecation
- Production status
- Quality metrics

### Result

The platform can answer:

> **Can I trust this capability?**

---

## Phase 5 - Agent-Native Capability

**Goal:** Make the manifest useful to AI agents, not just developers.

The manifest should contain enough semantic information for an agent to understand:

```text
What does this do?
When should I use it?
What inputs does it require?
What does it produce?
What dependencies does it have?
What data can it access?
What are its constraints?
How reliable is it?
```

Potential addition:

```yaml
semantics:

  purpose: >
    Analyze invoices and identify anomalies.

  use_when:
    - validating supplier invoices
    - detecting duplicate charges
    - identifying unusual amounts

  avoid_when:
    - invoice is handwritten
    - currency is unsupported
```

### Build

- Semantic descriptions
- Agent-readable metadata
- Capability search metadata
- Intent / use-case tags
- Capability compatibility
- Machine-readable constraints

### Result

An agent can eventually ask:

```text
"I need something that can detect
duplicate invoices."
```

and the platform can find:

```text
invoice-analyzer@2.1
```

without the developer explicitly wiring it in.

---

# The Evolution

The manifest grows in this order:

```text
PHASE 0
Identity
    
"What am I?"

PHASE 1
Interface
    
"What can I do?"

PHASE 2
Runtime + Dependencies
    
"How do I run?"

PHASE 3
Permissions + Data
    
"What can I access?"

PHASE 4
Evaluation + Governance
    
"Can I trust/use me?"

PHASE 5
Semantics
    
"When should an agent use me?"
```

The resulting capability becomes:

```text
ÚÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ¿
³          Capability           ³
ÃÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ´
³ Identity                      ³
³ Interface                     ³
³ Runtime                       ³
³ Dependencies                  ³
³ Configuration                 ³
³ Permissions                   ³
³ Data requirements             ³
³ Credentials                   ³
³ Evaluation                    ³
³ Governance                    ³
³ Lifecycle                     ³
³ Semantics                     ³
ÀÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÙ
```

---

# Recommended Delivery Strategy

## MVP - Phase 0 + Phase 1

Get to:

> **"I can define, register, discover, and invoke a standardized capability."**

Focus on:

- Capability schema
- Metadata
- Versioning
- Ownership
- Interfaces
- MCP
- Basic discovery
- Basic invocation

---

## V1 - Phase 2 + Phase 3

Get to:

> **"I can declare what my capability needs, and the platform can securely run it."**

Focus on:

- Runtime definitions
- Containers
- Dependencies
- Dependency resolution
- Secrets
- Permissions
- Identity
- Credential brokerage
- Basic policy enforcement

---

## V2 - Phase 4

Get to:

> **"I can confidently put this capability into production."**

Focus on:

- Evaluations
- Quality gates
- Security review
- Lifecycle management
- Production certification
- Observability
- Deprecation

---

## V3 - Phase 5

Get to:

> **"Agents can discover and select capabilities themselves."**

Focus on:

- Semantic metadata
- Intent-based search
- Agent-readable capability descriptions
- Capability compatibility
- Dynamic discovery
- Agent-driven capability selection

---

# Overall Strategy

The phases fall into three major layers:

```text
ÚÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ¿
³           V3 - AI-Native                ³
³                                         ³
³  Agents discover and compose            ³
³  capabilities dynamically               ³
ÀÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÂÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÙ
                     ³
ÚÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ¿
³           V2 - Enterprise               ³
³                                         ³
³  Evaluation + governance + security     ³
ÀÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÂÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÙ
                     ³
ÚÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ¿
³           V1 - Platform                 ³
³                                         ³
³  Define + discover + run + reuse        ³
ÀÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÙ
```

The key architectural principle is:

> **Start with a simple capability contract, then progressively add execution, security, governance, and intelligence around that contract.**

This prevents the initial platform from becoming an overly complex "AI operating system" before you've proven that teams actually want to **share and reuse capabilities**.

# Example Capability

```yaml
apiVersion: ai.company/v1
kind: Capability
metadata:
  name: customer-risk-analysis
  version: 3.2.1
  displayName: Customer Risk Analysis
  description: >
    Analyze customer behavior and generate a customer
    risk assessment.
  tags:
    - customer
    - risk
    - analytics

ownership:
  team: Customer AI
  organization: Revenue Technology

interface:
  type: mcp
  tools:
    - name: analyze_customer
      description: >
        Analyze a customer's current risk profile.
      input:
        type: object
        properties:
          customer_id:
            type: string
        required:
          - customer_id
      output:
        type: object

runtime:
  type: container
  image:
    repository: registry.company.com/ai/customer-risk
    tag: 3.2.1

dependencies:
  capabilities:
    - customer.lookup: "^2.0"
    - churn.predict: "^4.1"
  services:
    - salesforce
  packages:
    python:
      - pandas
      - scikit-learn

credentials:
  - name: salesforce
    type: oauth
    scopes:
      - customer.read

permissions:
  data:
    - customer.profile.read
    - customer.transaction.read
  services:
    - salesforce.customer.read

data:
  input:
    classification: confidential
  output:
    classification: confidential

configuration:
  parameters:
    threshold:
      type: number
      default: 0.75

governance:
  humanApproval:
    required: false

evaluation:
  suite: customer-risk-v3
  requirements:
    accuracy:
      minimum: 0.90
    latency:
      p95:
        maximum: 5s

lifecycle:
  stage: production
  status: active

```

```mermaid
graph TD
    subgraph "Input Layer"
        A["📄 User Query / Document"]
        B["📁 Document Upload"]
    end

    subgraph "Retrieval & Processing"
        C["🔍 Hybrid Search<br/>Lexical + Semantic"]
        D["🧠 Self-RAG<br/>CRAG<br/>HyDE"]
        E["↔️ Qdrant Vector DB<br/>Semantic Storage"]
    end

    subgraph "Ranking & Filtering"
        F["🔗 Cross-Encoder<br/>Reranking"]
        G["🛡️ 9-Layer<br/>Guardrails"]
    end

    subgraph "LLM & Generation"
        H["🤖 LLM<br/>Query Processing"]
        I["💾 Semantic<br/>Caching"]
        J["🗄️ SQL Query<br/>Generation"]
    end

    subgraph "Approval & Output"
        K["👤 Human Approval<br/>Workflow"]
        L["✅ Final Response"]
    end

    subgraph "API & Orchestration"
        M["⚡ FastAPI<br/>REST Endpoints"]
        N["🔗 LangGraph<br/>Workflow Engine"]
    end

    subgraph "Infrastructure"
        O["☸️ Kubernetes<br/>Deployment"]
        P["🐳 Docker<br/>Containerization"]
    end

    A --> C
    B --> E
    C --> E
    D --> E
    E --> F
    F --> G
    G --> H
    H --> I
    H --> J
    J --> K
    K --> L
    
    M -.->|Orchestrates| N
    N -.->|Manages| C
    N -.->|Manages| D
    N -.->|Manages| G
    N -.->|Manages| H
    
    M --> L
    L --> O
    O --> P

    style A fill:#e1f5ff
    style B fill:#e1f5ff
    style C fill:#f3e5f5
    style D fill:#f3e5f5
    style E fill:#fff3e0
    style F fill:#f3e5f5
    style G fill:#ffebee
    style H fill:#e8f5e9
    style I fill:#fff9c4
    style J fill:#e8f5e9
    style K fill:#fce4ec
    style L fill:#c8e6c9
    style M fill:#e0e2f1
    style N fill:#e0e2f1
    style O fill:#b2dfdb
    style P fill:#b2dfdb
```

## Technology Stack Summary

| Component | Technologies |
|-----------|--------------|
| **Backend Framework** | FastAPI, LangGraph |
| **Vector Database** | Qdrant |
| **LLM & RAG** | Self-RAG, CRAG, HyDE |
| **Retrieval** | Hybrid Search (Lexical + Semantic) |
| **Ranking** | Cross-Encoder Reranking |
| **Query Generation** | SQL Query with Human Approval |
| **Security** | 9-Layer Guardrails |
| **Performance** | Semantic Caching |
| **Languages** | Python (81.8%), JavaScript (7.6%), CSS (5.4%) |
| **Deployment** | Kubernetes, Docker |

## End-to-End Flow

1. **Input** → User submits query or uploads document
2. **Retrieval** → Hybrid search combines lexical and semantic methods
3. **Vector Storage** → Qdrant stores and retrieves semantic embeddings
4. **Advanced RAG** → Self-RAG, CRAG, and HyDE process documents intelligently
5. **Ranking** → Cross-encoder reranks results for relevance
6. **Safety** → 9-layer guardrails filter malicious/harmful content
7. **LLM Processing** → Language model generates queries/responses with caching
8. **Approval** → SQL queries and critical operations require human approval
9. **Response** → Final output delivered via FastAPI REST endpoints
10. **Deployment** → Runs on Kubernetes with Docker containerization
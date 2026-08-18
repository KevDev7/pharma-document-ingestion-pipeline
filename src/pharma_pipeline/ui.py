import gradio as gr
import httpx


def _trim_excerpt(text: str, limit: int = 650) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "..."


def format_search_response(payload: dict[str, object]) -> str:
    results = payload.get("results", [])
    if not results:
        return (
            "I could not find a matching passage in the current document versions. "
            "Try a more specific term or remove the document-type filter."
        )

    lines = [
        (
            f"Found **{len(results)} relevant passage(s)** in "
            f"{float(payload['latency_ms']):.2f} ms."
        ),
        "",
    ]
    for index, result in enumerate(results, start=1):
        lines.extend(
            [
                f"### {index}. {result['filename']} · page {result['page_number']}",
                _trim_excerpt(str(result["text"])),
                "",
                (
                    f"`{result['document_type']}` · relative BM25 score "
                    f"`{float(result['score']):.4f}`"
                ),
                "",
            ]
        )
    lines.append(
        "BM25 scores rank results for this query; they are not confidence probabilities."
    )
    return "\n".join(lines)


def search_api(
    message: str,
    history: list[dict[str, str]] | None,
    api_url: str,
    top_k: int,
    document_type: str,
) -> tuple[list[dict[str, str]], str]:
    cleaned = message.strip()
    current_history = list(history or [])
    if not cleaned:
        return current_history, ""

    request = {"query": cleaned, "top_k": int(top_k)}
    if document_type and document_type != "All document types":
        request["document_type"] = document_type

    try:
        response = httpx.post(
            f"{api_url.rstrip('/')}/search",
            json=request,
            timeout=30.0,
        )
        response.raise_for_status()
        answer = format_search_response(response.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        answer = (
            "The retrieval API could not complete this search. "
            f"Check that it is running at `{api_url}`.\n\n`{type(error).__name__}`"
        )

    current_history.extend(
        [
            {"role": "user", "content": cleaned},
            {"role": "assistant", "content": answer},
        ]
    )
    return current_history, ""


def load_document_types(api_url: str) -> gr.Dropdown:
    choices = ["All document types"]
    try:
        response = httpx.get(f"{api_url.rstrip('/')}/document-types", timeout=10.0)
        response.raise_for_status()
        choices.extend(response.json())
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    return gr.Dropdown(choices=choices, value=choices[0])


def create_demo(api_url: str = "http://127.0.0.1:8000") -> gr.Blocks:
    with gr.Blocks(title="Pharmaceutical Document Search") as demo:
        gr.Markdown(
            "# Pharmaceutical Document Search\n"
            "Search the current processed PDFs and inspect the exact source passages."
        )
        with gr.Row():
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    label="Retrieved evidence",
                    type="messages",
                    height=420,
                    show_copy_button=True,
                )
                question = gr.Textbox(
                    label="Question",
                    placeholder="What must be completed before commercial distribution?",
                    lines=2,
                )
                with gr.Row():
                    clear = gr.Button("Clear")
                    submit = gr.Button("Search", variant="primary")
            with gr.Column(scale=1, min_width=260):
                gr.Markdown("## Retrieval settings")
                document_type = gr.Dropdown(
                    ["All document types"],
                    value="All document types",
                    label="Document type",
                )
                top_k = gr.Slider(1, 10, value=5, step=1, label="Passages to retrieve")
                gr.Markdown(
                    "Results come from the SQLite FTS5/BM25 index. "
                    "Each passage keeps its source file and page."
                )

        inputs = [question, chatbot, top_k, document_type]
        submit.click(
            lambda message, history, k, doc_type: search_api(
                message, history, api_url, k, doc_type
            ),
            inputs=inputs,
            outputs=[chatbot, question],
        )
        question.submit(
            lambda message, history, k, doc_type: search_api(
                message, history, api_url, k, doc_type
            ),
            inputs=inputs,
            outputs=[chatbot, question],
        )
        clear.click(lambda: ([], ""), outputs=[chatbot, question])
        demo.load(
            lambda: load_document_types(api_url),
            outputs=document_type,
        )
    return demo

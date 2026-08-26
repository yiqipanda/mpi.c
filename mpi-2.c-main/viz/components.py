from dash import dcc, html
import plotly.express as px
import pandas as pd

class Visualizer:
    """
    Creates reusable Dash UI components and Plotly visualizations.
    """

    @staticmethod
    def create_header():
        return html.Div(
            className="dashboard-header",
            children=[
                html.Div(
                    [
                        html.H1("MPI Simulator Analytics"),
                        html.P(
                            "Trace and simulation performance dashboard",
                            className="subtitle",
                        ),
                    ]
                ),
                html.Div(
                    [
                        html.Span("●", className="status-dot"),
                        html.Span("READY", className="status-text"),
                    ],
                    className="status-badge",
                ),
            ],
        )

    @staticmethod
    def create_kpi_card(title, value, description, card_class=""):
        return html.Div(
            className=f"kpi-card {card_class}",
            children=[
                html.Div(title, className="kpi-title"),
                html.Div(value, className="kpi-value"),
                html.Div(description, className="kpi-description"),
            ],
        )

    @staticmethod
    def create_kpi_section(df: pd.DataFrame):
        if df.empty:
            return html.Div(
                [
                    Visualizer.create_kpi_card(
                        "Processes", "0", "MPI ranks"
                    ),
                    Visualizer.create_kpi_card(
                        "Events", "0", "Trace events"
                    ),
                    Visualizer.create_kpi_card(
                        "Duration", "0 ms", "Execution time"
                    ),
                    Visualizer.create_kpi_card(
                        "Messages", "0", "MPI events"
                    ),
                ],
                className="kpi-grid",
            )

        process_count = (
            df["tid"].nunique()
            if "tid" in df.columns
            else 0
        )

        event_count = len(df)

        if "ts_ms" in df.columns:
            duration = df["ts_ms"].max() - df["ts_ms"].min()
        else:
            duration = 0

        message_count = 0

        if "name" in df.columns:
            message_keywords = [
                "send",
                "recv",
                "message",
                "mpi_send",
                "mpi_recv",
            ]

            message_count = sum(
                df["name"]
                .astype(str)
                .str.lower()
                .apply(
                    lambda name: any(
                        keyword in name
                        for keyword in message_keywords
                    )
                )
            )

        return html.Div(
            [
                Visualizer.create_kpi_card(
                    "Processes",
                    str(process_count),
                    "MPI ranks",
                ),
                Visualizer.create_kpi_card(
                    "Events",
                    f"{event_count:,}",
                    "Trace events",
                ),
                Visualizer.create_kpi_card(
                    "Duration",
                    f"{duration:.2f} ms",
                    "Execution time",
                ),
                Visualizer.create_kpi_card(
                    "Messages",
                    f"{message_count:,}",
                    "MPI events",
                ),
            ],
            className="kpi-grid",
        )

    @staticmethod
    def create_timeline_chart(df: pd.DataFrame):
        if df.empty:
            return html.Div(
                "No trace data available.",
                className="empty-state",
            )

        required_columns = {"ts_ms", "tid", "name"}

        if not required_columns.issubset(df.columns):
            return html.Div(
                "Trace data does not contain the required fields.",
                className="empty-state",
            )

        fig = px.scatter(
            df,
            x="ts_ms",
            y="tid",
            color="name",
            hover_data=[
                column
                for column in ["cat", "pid", "ph"]
                if column in df.columns
            ],
            title=None,
            labels={
                "ts_ms": "Time (ms)",
                "tid": "Rank",
            },
        )

        fig.update_layout(
            template="plotly_white",
            paper_bgcolor="white",
            plot_bgcolor="white",
            margin=dict(l=50, r=20, t=20, b=50),
            legend_title_text="Event",
            font=dict(
                family="Inter, Arial, sans-serif",
                color="#1f2937",
            ),
        )

        fig.update_yaxes(
            autorange="reversed",
            dtick=1,
        )

        fig.update_traces(
            marker=dict(size=9),
        )

        return dcc.Graph(
            figure=fig,
            config={
                "displayModeBar": True,
                "displaylogo": False,
            },
        )

    @staticmethod
    def create_statistics(df: pd.DataFrame):
        if df.empty or "name" not in df.columns:
            return html.Div(
                "No statistics available.",
                className="empty-state",
            )

        counts = (
            df["name"]
            .astype(str)
            .value_counts()
            .reset_index()
        )

        counts.columns = ["Event", "Count"]

        fig = px.bar(
            counts,
            x="Event",
            y="Count",
            color="Count",
            title=None,
            color_continuous_scale="Blues",
        )

        fig.update_layout(
            template="plotly_white",
            paper_bgcolor="white",
            plot_bgcolor="white",
            margin=dict(l=40, r=20, t=20, b=80),
            showlegend=False,
            font=dict(
                family="Inter, Arial, sans-serif",
                color="#1f2937",
            ),
        )

        return dcc.Graph(
            figure=fig,
            config={
                "displayModeBar": False,
                "displaylogo": False,
            },
        )

    @staticmethod
    def create_rank_statistics(df: pd.DataFrame):
        if df.empty or "tid" not in df.columns:
            return html.Div(
                "No rank statistics available.",
                className="empty-state",
            )

        rank_stats = (
            df.groupby("tid")
            .size()
            .reset_index(name="Events")
            .sort_values("tid")
        )

        rank_stats.columns = ["Rank", "Events"]

        return html.Div(
            [
                html.Div(
                    [
                        html.Div("Rank", className="table-header"),
                        html.Div("Events", className="table-header"),
                    ],
                    className="stats-row stats-header",
                )
            ]
            + [
                html.Div(
                    [
                        html.Div(str(row["Rank"])),
                        html.Div(f'{row["Events"]:,}'),
                    ],
                    className="stats-row",
                )
                for _, row in rank_stats.iterrows()
            ],
            className="stats-table",
        )

    @staticmethod
    def create_log_view(log_content: str):
        if not log_content:
            log_content = "No log data available."

        return html.Pre(
            log_content,
            className="log-view",
        )


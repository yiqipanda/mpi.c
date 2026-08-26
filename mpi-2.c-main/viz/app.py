import datetime

import dash
from dash import dcc, html, Input, Output

from viz.data_loader import TraceLoader, LogLoader
from viz.components import Visualizer

# ---------------------------------------------------------

# Application

# ---------------------------------------------------------

app = dash.Dash(
    __name__,title="MPI Simulator Analytics",
    assets_folder="../assets",
    )

server = app.server

# ---------------------------------------------------------

# Paths

# ---------------------------------------------------------

TRACE_PATH = "trace.json"
LOG_PATH = "logs/mpi_sim.log"

# ---------------------------------------------------------

# Layout

# ---------------------------------------------------------

app.layout = html.Div(
className="app-container",
children=[


    # Header
    Visualizer.create_header(),

    # Controls
    html.Div(
        className="control-bar",
        children=[
            html.Button(
                "↻  Refresh Data",
                id="btn-refresh",
                n_clicks=0,
                className="refresh-button",
            ),

            html.Div(
                id="last-updated",
                className="last-updated",
                children="Waiting for data...",
            ),
        ],
    ),

    # KPI Cards
    html.Div(
        id="kpi-container",
        children=Visualizer.create_kpi_section(
            TraceLoader(TRACE_PATH).get_dataframe()
        ),
    ),

    # Main tabs
    dcc.Tabs(
        id="main-tabs",
        value="timeline",
        className="main-tabs",
        children=[

            dcc.Tab(
                label="Timeline",
                value="timeline",
                className="tab",
                selected_className="tab-selected",
                children=[
                    html.Div(
                        className="tab-content",
                        children=[
                            html.Div(
                                className="section-title",
                                children=[
                                    html.H2(
                                        "MPI Rank Activity"
                                    ),
                                    html.P(
                                        "Event activity across MPI ranks over time."
                                    ),
                                ],
                            ),
                            html.Div(
                                id="timeline-container",
                                className="chart-card",
                            ),
                        ],
                    )
                ],
            ),

            dcc.Tab(
                label="Statistics",
                value="statistics",
                className="tab",
                selected_className="tab-selected",
                children=[
                    html.Div(
                        className="tab-content",
                        children=[
                            html.Div(
                                className="statistics-grid",
                                children=[

                                    html.Div(
                                        className="chart-card",
                                        children=[
                                            html.Div(
                                                className="section-title",
                                                children=[
                                                    html.H2(
                                                        "Event Distribution"
                                                    ),
                                                    html.P(
                                                        "Number of occurrences by event type."
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                id="stats-container"
                                            ),
                                        ],
                                    ),

                                    html.Div(
                                        className="chart-card",
                                        children=[
                                            html.Div(
                                                className="section-title",
                                                children=[
                                                    html.H2(
                                                        "Rank Statistics"
                                                    ),
                                                    html.P(
                                                        "Event count per MPI rank."
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                id="rank-stats-container"
                                            ),
                                        ],
                                    ),

                                ],
                            )
                        ],
                    )
                ],
            ),

            dcc.Tab(
                label="Logs",
                value="logs",
                className="tab",
                selected_className="tab-selected",
                children=[
                    html.Div(
                        className="tab-content",
                        children=[
                            html.Div(
                                className="section-title",
                                children=[
                                    html.H2("Simulation Logs"),
                                    html.P(
                                        "Latest MPI simulator output."
                                    ),
                                ],
                            ),
                            html.Div(
                                id="logs-container",
                                className="chart-card",
                            ),
                        ],
                    )
                ],
            ),
        ],
    ),
],


)

# ---------------------------------------------------------

# Callback

# ---------------------------------------------------------

@app.callback(
[
Output("kpi-container", "children"),
Output("timeline-container", "children"),
Output("stats-container", "children"),
Output("rank-stats-container", "children"),
Output("logs-container", "children"),
Output("last-updated", "children"),
],
Input("btn-refresh", "n_clicks"),
)
def update_dashboard(n_clicks):
    trace_loader = TraceLoader(TRACE_PATH)
    log_loader = LogLoader(LOG_PATH)

    df = trace_loader.get_dataframe()
    log_content = log_loader.read_logs()

    kpis = Visualizer.create_kpi_section(df)
    timeline = Visualizer.create_timeline_chart(df)
    statistics = Visualizer.create_statistics(df)
    rank_statistics = Visualizer.create_rank_statistics(df)
    logs = Visualizer.create_log_view(log_content)

    now = datetime.datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    return (
        kpis,
        timeline,
        statistics,
        rank_statistics,
        logs,
        f"Last updated: {now}",
    )


# ---------------------------------------------------------

# Run

# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(
        debug=True,
        port=8050,
    )

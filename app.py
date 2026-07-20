"""
进度计划智能助手（重构版）
设计：
- 右侧嵌入 IFRAME 聊天框（Dify 提供的对话窗口），负责"用户输入 → AI 思考 → 返回 JSON 文本"
- 左侧/下方为本应用模块，负责"用户传入 JSON 进度计划文件 → 展示各类图表"
- 两个模块完全解耦。用户从聊天框得到 JSON 文本后，保存为 .json 文件，再上传到本应用渲染。
- 历史文件以设备为单位保存；同名的文件视为同一文件，不再重复保存。
开发者：智建领航小组 · 华南理工大学
"""

import json
import os
import re
from datetime import datetime
from io import BytesIO

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

# ==================== IFRAME 配置 ====================
# 来自 iframe.json：Dify 提供的聊天框嵌入代码
IFRAME_URL = "https://udify.app/chatbot/0LgGb0kd3QAMpgN0"


# ==================== 数据加载与解析 ====================

def load_json_from_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_json_from_upload(uploaded_file_content):
    return json.loads(uploaded_file_content.decode("utf-8"))


def validate_data_structure(data):
    """校验 JSON 数据结构，兼容两种形态：
    1) 完整结构：{ "structured_output": { overview, all_tasks_schedule, ... } }
    2) 简化结构：直接是 { overview, all_tasks_schedule, ... }
    """
    if "structured_output" in data and isinstance(data["structured_output"], dict):
        structured = data["structured_output"]
    elif "overview" in data and "all_tasks_schedule" in data:
        structured = data
    else:
        return False, "数据缺少 'overview' 或 'all_tasks_schedule' 字段"

    if "overview" not in structured:
        return False, "缺少 'overview' 字段"
    overview = structured["overview"]
    for field in ["project_name", "total_duration_days", "planned_start_date", "planned_end_date"]:
        if field not in overview:
            return False, f"overview 缺少 '{field}' 字段"

    tasks = structured.get("all_tasks_schedule", [])
    if not isinstance(tasks, list) or len(tasks) == 0:
        return False, "all_tasks_schedule 为空或不是列表"
    for i, task in enumerate(tasks):
        for field in ["task_id", "task_name", "start_date", "finish_date", "duration_days"]:
            if field not in task:
                return False, f"第 {i+1} 个任务缺少 '{field}' 字段"
    return True, "数据格式验证通过"


def normalize_to_wrapped(data):
    """统一为 { structured_output: ... } 结构，供后续渲染函数使用。"""
    if "structured_output" in data and isinstance(data["structured_output"], dict):
        return data
    return {"structured_output": data}


# ==================== 历史文件管理（以设备为单位，按文件名去重）====================

def get_history_directory():
    """获取历史文件目录，按优先级尝试多个可写位置。"""
    candidates = []
    try:
        app_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(app_dir, "uploaded_history"))
    except Exception:
        pass
    try:
        candidates.append(os.path.join(os.getcwd(), "uploaded_history"))
    except Exception:
        pass
    try:
        candidates.append(os.path.join(os.path.expanduser("~"), "progress_plan_history"))
    except Exception:
        pass
    tmp_base = os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"
    candidates.append(os.path.join(tmp_base, "progress_plan_history"))

    last_error = None
    for history_dir in candidates:
        try:
            if not os.path.exists(history_dir):
                os.makedirs(history_dir)
            test_path = os.path.join(history_dir, ".write_test")
            with open(test_path, "w", encoding="utf-8") as f:
                f.write("test")
            os.remove(test_path)
            return history_dir
        except Exception as e:
            last_error = e
            continue
    st.error(f"无法创建历史文件目录：{str(last_error)}")
    return None


def check_history_file_exists(file_name):
    history_dir = get_history_directory()
    if history_dir is None:
        return False
    if not file_name.endswith(".json"):
        file_name = file_name + ".json"
    return os.path.exists(os.path.join(history_dir, file_name))


def save_file_unique(file_name, file_content):
    """按文件名去重保存：
    - 若历史中已有同名文件，返回 (False, "已存在")，不覆盖；
    - 若新文件名，保存并返回 (True, save_path)。
    """
    history_dir = get_history_directory()
    if history_dir is None:
        return False, "历史文件目录不可用"
    if not file_name.endswith(".json"):
        file_name = file_name + ".json"
    save_path = os.path.join(history_dir, file_name)
    if os.path.exists(save_path):
        return False, f"历史中已存在同名文件 '{file_name}'，按规则视为同一文件，不重复保存"
    try:
        with open(save_path, "wb") as f:
            f.write(file_content)
        return True, save_path
    except Exception as e:
        return False, f"保存失败：{str(e)}"


def get_history_json_files():
    history_dir = get_history_directory()
    if history_dir is None or not os.path.exists(history_dir):
        return []
    files = []
    try:
        for f in os.listdir(history_dir):
            if f.endswith(".json"):
                file_path = os.path.join(history_dir, f)
                try:
                    mtime = os.path.getmtime(file_path)
                    upload_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    upload_time = "未知"
                project_name = f[:-5] if f.endswith(".json") else f
                files.append({
                    "file_name": f,
                    "file_path": file_path,
                    "project_name": project_name,
                    "upload_time": upload_time,
                })
        files.sort(key=lambda x: x["upload_time"], reverse=True)
    except Exception:
        return []
    return files


def delete_history_file(file_path):
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        return True
    except Exception:
        return False


# ==================== 数据处理 ====================

def extract_section_from_task_id(task_id):
    parts = str(task_id).split(".")
    return parts[0] if parts else "0"


def get_section_mapping(tasks):
    sections = {}
    section_names = {
        "1": "施工准备", "2": "地基与基础", "3": "主体结构",
        "4": "建筑装饰装修", "5": "建筑屋面", "6": "建筑给水排水",
        "7": "建筑电气", "8": "智能建筑", "9": "建筑节能与消防",
        "10": "室外工程", "11": "竣工验收",
    }
    for task in tasks:
        code = extract_section_from_task_id(task["task_id"])
        if code not in sections:
            sections[code] = section_names.get(code, f"分部{code}")
    return dict(sorted(sections.items()))


def get_critical_task_ids(critical_path_tasks):
    return {task["task_id"] for task in critical_path_tasks}


def tasks_to_dataframe(tasks, critical_task_ids):
    df = pd.DataFrame(tasks)
    df["is_critical"] = df["task_id"].isin(critical_task_ids)
    df["section_code"] = df["task_id"].apply(extract_section_from_task_id)
    df["start_date"] = pd.to_datetime(df["start_date"])
    df["finish_date"] = pd.to_datetime(df["finish_date"])
    df = df.sort_values(["start_date", "task_id"]).reset_index(drop=True)
    return df


def calculate_daily_resources(tasks_df):
    start_date = tasks_df["start_date"].min()
    end_date = tasks_df["finish_date"].max()
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")
    daily_manpower = pd.Series(0, index=date_range, dtype=float)
    daily_detail = {d: {} for d in date_range}

    for _, task in tasks_df.iterrows():
        resources = task.get("assigned_resources", {})
        if not isinstance(resources, dict):
            continue
        mask = (date_range >= task["start_date"]) & (date_range <= task["finish_date"])
        active_dates = date_range[mask]
        for resource_name, count in resources.items():
            if not isinstance(count, (int, float)):
                continue
            count = int(count)
            daily_manpower.loc[active_dates] += count
            for d in active_dates:
                daily_detail[d][resource_name] = daily_detail[d].get(resource_name, 0) + count

    detail_texts = []
    for d in date_range:
        items = daily_detail[d]
        if items:
            lines = "<br>".join([f"  {k}: {v}" for k, v in sorted(items.items())])
            detail_texts.append(lines)
        else:
            detail_texts.append("无")
    return date_range, daily_manpower.tolist(), detail_texts


# ==================== 图表绘制 ====================

def _format_cn_date(dt):
    try:
        return f"{dt.year}年{dt.month}月{dt.day}日"
    except Exception:
        return str(dt)


def _format_short_date(dt):
    try:
        return f"{int(dt.month)}/{int(dt.day)}"
    except Exception:
        return str(dt)


def _build_gantt_data(tasks_df, section_filter=None):
    if section_filter and len(section_filter) > 0:
        filtered_df = tasks_df[tasks_df["section_code"].isin(section_filter)].copy()
    else:
        filtered_df = tasks_df.copy()
    if len(filtered_df) == 0:
        return None

    plot_df = filtered_df.copy()
    plot_df["Start"] = pd.to_datetime(plot_df["start_date"])
    plot_df["Finish"] = pd.to_datetime(plot_df["finish_date"])
    plot_df["_sec_num"] = plot_df["section_code"].astype(int)
    plot_df = plot_df.sort_values(["_sec_num", "task_id"]).reset_index(drop=True)

    sections = []
    for code, grp in plot_df.groupby("_sec_num", sort=True):
        sec_start = grp["Start"].min()
        sec_finish = grp["Finish"].max()
        sec_duration = (sec_finish - sec_start).days + 1
        sections.append({
            "code": str(int(code)),
            "count": len(grp),
            "Start": sec_start,
            "Finish": sec_finish,
            "duration": sec_duration,
            "children": grp,
        })
    sections.sort(key=lambda s: int(s["code"]))

    ordered_rows = []
    for sec in sections:
        ordered_rows.append({
            "label": f"#{sec['code']}#（含{sec['count']}道工序）",
            "Start": sec["Start"],
            "Finish": sec["Finish"],
            "duration": sec["duration"],
            "row_type": "section",
            "bar_color": "#000000",
            "resources": None,
            "task_id": sec["code"],
            "task_name": f"分部{sec['code']}",
        })
        for _, t in sec["children"].iterrows():
            resources = t.get("assigned_resources", {})
            ordered_rows.append({
                "label": f"  {t['task_id']} {t['task_name']}",
                "Start": t["Start"],
                "Finish": t["Finish"],
                "duration": t["duration_days"],
                "row_type": "task",
                "bar_color": "#e74c3c",
                "resources": resources if isinstance(resources, dict) else {},
                "task_id": t["task_id"],
                "task_name": t["task_name"],
            })
    return pd.DataFrame(ordered_rows)


def _build_resource_hover_text(resources):
    if not resources or not isinstance(resources, dict):
        return "无资源配置"
    lines = [f"  {k}: {v}" for k, v in sorted(resources.items())]
    return "<br>".join(lines)


def create_gantt_chart(tasks_df, milestones, section_filter=None, show_milestones=True):
    rows_df = _build_gantt_data(tasks_df, section_filter=section_filter)
    if rows_df is None or len(rows_df) == 0:
        fig = go.Figure()
        fig.update_layout(title="施工进度甘特图（暂无数据）")
        return fig

    y_order = rows_df["label"].tolist()
    n_rows = len(rows_df)
    fig = go.Figure()

    black_rows = rows_df[rows_df["bar_color"] == "#000000"]
    red_rows = rows_df[rows_df["bar_color"] == "#e74c3c"]

    for label_set, color, name in [
        (black_rows, "#000000", "分部大类"),
        (red_rows, "#e74c3c", "分部小类"),
    ]:
        if len(label_set) == 0:
            continue
        x_durations = [
            (row["Finish"] - row["Start"]).total_seconds() * 1000
            for _, row in label_set.iterrows()
        ]
        fig.add_trace(go.Bar(
            x=x_durations,
            y=label_set["label"].tolist(),
            base=[d.to_pydatetime() for d in label_set["Start"]],
            orientation="h",
            marker=dict(color=color, line=dict(color="#333", width=0.5)),
            name=name,
            showlegend=True,
            hoverinfo="skip",
            width=0.6,
        ))

    date_min = rows_df["Start"].min()
    date_max = rows_df["Finish"].max()
    all_dates = pd.date_range(start=date_min, end=date_max, freq="D")
    red_tasks_list = red_rows.to_dict("records")

    task_hover_cache = {}
    for t in red_tasks_list:
        res_text = _build_resource_hover_text(t["resources"])
        task_hover_cache[t["task_id"]] = (
            f"<b>{t['task_id']} {t['task_name']}</b><br>"
            f"工期：{t['duration']}天<br>"
            f"资源配置：{res_text}"
        )

    date_to_hover = {}
    for t in red_tasks_list:
        task_dates = pd.date_range(start=t["Start"], end=t["Finish"], freq="D")
        hover = task_hover_cache[t["task_id"]]
        for d in task_dates:
            d_key = d.strftime("%Y-%m-%d")
            if d_key not in date_to_hover:
                date_to_hover[d_key] = []
            date_to_hover[d_key].append(hover)

    daily_hover_texts = []
    for d in all_dates:
        d_key = d.strftime("%Y-%m-%d")
        if d_key in date_to_hover:
            daily_hover_texts.append("<br>".join(date_to_hover[d_key]))
        else:
            daily_hover_texts.append("当天无进行中的小类工序")

    fig.add_trace(go.Scatter(
        x=all_dates,
        y=[y_order[len(y_order) // 2]] * len(all_dates),
        mode="markers",
        marker=dict(color="rgba(0,0,0,0)", size=1),
        text=daily_hover_texts,
        hoverinfo="text",
        showlegend=False,
        hovertemplate="%{text}<extra></extra>",
    ))

    annotations = []
    for _, row in rows_df.iterrows():
        start_dt = row["Start"].to_pydatetime()
        finish_dt = row["Finish"].to_pydatetime()
        annotations.append(dict(
            x=start_dt, y=row["label"],
            text=_format_short_date(start_dt),
            showarrow=False, xanchor="right", yanchor="middle",
            xshift=-5,
            font=dict(size=9, color="#333", family="Microsoft YaHei"),
        ))
        annotations.append(dict(
            x=finish_dt, y=row["label"],
            text=_format_short_date(finish_dt),
            showarrow=False, xanchor="left", yanchor="middle",
            xshift=5,
            font=dict(size=9, color="#333", family="Microsoft YaHei"),
        ))

    tick_texts = []
    for _, row in rows_df.iterrows():
        if row["row_type"] == "section":
            tick_texts.append(
                f"<b>{row['label']}　{_format_cn_date(row['Start'])}–{_format_cn_date(row['Finish'])}　{row['duration']}d</b>"
            )
        else:
            tick_texts.append(
                f"{row['label']}　{_format_cn_date(row['Start'])}–{_format_cn_date(row['Finish'])}　{row['duration']}d"
            )

    if show_milestones and milestones:
        for milestone in milestones:
            md = pd.Timestamp(milestone["date"]).to_pydatetime()
            fig.add_trace(go.Scatter(
                x=[md], y=[y_order[0]], mode="markers",
                marker=dict(
                    symbol="diamond", size=14, color="#f39c12",
                    line=dict(color="#d68910", width=2)
                ),
                showlegend=False,
                hovertemplate=(
                    f"<b>里程碑：{milestone['name']}</b><br>"
                    f"日期：{milestone['date']}<extra></extra>"
                ),
            ))
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=12, color="#f39c12", symbol="diamond"),
            name="里程碑", showlegend=True,
        ))

    height = max(600, n_rows * 28 + 200)
    fig.update_layout(
        title=dict(text="施工进度甘特图", font=dict(size=18, family="Microsoft YaHei"), x=0.5, xanchor="center"),
        barmode="overlay",
        height=height,
        margin=dict(l=80, r=80, t=100, b=100),
        plot_bgcolor="white",
        paper_bgcolor="white",
        annotations=annotations,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(family="Microsoft YaHei")),
        hoverlabel=dict(font=dict(family="Microsoft YaHei", size=12), bgcolor="white", bordercolor="#ddd"),
    )
    fig.update_yaxes(
        categoryorder="array",
        categoryarray=y_order[::-1],
        ticktext=tick_texts[::-1],
        tickvals=y_order[::-1],
        tickfont=dict(size=10, family="Microsoft YaHei"),
        gridcolor="rgba(0,0,0,0.05)",
        showgrid=True, zeroline=False,
        side="left",
    )
    fig.update_xaxes(
        type="date",
        tickformat="%Y年%m月%d日",
        hoverformat="%Y年%m月%d日",
        tickangle=-45,
        gridcolor="rgba(0,0,0,0.1)",
        showgrid=True, zeroline=False,
        showticklabels=True,
        side="bottom",
        showspikes=True,
        spikecolor="#f1c40f",
        spikesnap="cursor",
        spikethickness=2,
        spikedash="solid",
    )
    fig.update_layout(
        xaxis2=dict(
            type="date",
            tickformat="%Y年%m月%d日",
            tickangle=-45,
            gridcolor="rgba(0,0,0,0)",
            showgrid=False, zeroline=False,
            side="top",
            overlaying="x",
            showticklabels=True,
            anchor="y",
            dtick="M1",
            showspikes=True,
            spikecolor="#f1c40f",
            spikesnap="cursor",
            spikethickness=2,
            spikedash="solid",
        ),
        hovermode="x unified",
        hoverlabel=dict(
            font=dict(family="Microsoft YaHei", size=12),
            bgcolor="rgba(241, 196, 15, 0.95)",
            bordercolor="#f1c40f",
        ),
    )
    return fig


def create_manpower_curve(tasks_df):
    date_range, daily_manpower, daily_detail_texts = calculate_daily_resources(tasks_df)
    x_dates = [pd.Timestamp(d).to_pydatetime() for d in date_range]
    y_vals = [int(v) for v in daily_manpower]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x_dates, y=y_vals, mode="lines", fill="tozeroy",
        line=dict(color="#27ae60", width=2),
        fillcolor="rgba(39, 174, 96, 0.3)",
        name="人力需求",
        customdata=daily_detail_texts,
        hovertemplate="<b>日期：%{x|%Y-%m-%d}</b><br>总人力：%{y}人<br><b>资源明细：</b><br>%{customdata}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="资源负荷曲线（人力）", font=dict(size=16, family="Microsoft YaHei"), x=0.5),
        xaxis=dict(title="日期", type="date", tickformat="%Y-%m-%d", tickangle=-45, gridcolor="rgba(0,0,0,0.1)", showgrid=True),
        yaxis=dict(title="人力（人）", gridcolor="rgba(0,0,0,0.1)", rangemode="tozero"),
        height=400,
        margin=dict(l=60, r=30, t=60, b=80),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hoverlabel=dict(font=dict(family="Microsoft YaHei")),
        showlegend=False,
    )
    return fig


# ==================== 界面展示 ====================

def render_project_overview(overview):
    st.markdown("### 📊 项目概览")
    st.markdown(
        f"<h2 style='font-weight: bold; color: #1e3a8a;'>{overview.get('project_name', '未知项目')}</h2>",
        unsafe_allow_html=True,
    )
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总工期", f"{overview.get('total_duration_days', 0)} 天")
    with col2:
        st.metric("计划开始", overview.get("planned_start_date", "暂无"))
    with col3:
        st.metric("计划完成", overview.get("planned_end_date", "暂无"))
    with col4:
        st.metric("关键路径工序数", f"{overview.get('critical_path_length', 0)} 项")


def _is_numeric_value(v):
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v.replace(",", ""))
            return True
        except (ValueError, AttributeError):
            return False
    return False


def _render_centered_table(df):
    if df is None or len(df) == 0:
        st.info("暂无数据")
        return
    columns = df.columns.tolist()
    numeric_cols = set()
    for col in columns:
        col_values = df[col].dropna()
        if len(col_values) == 0:
            continue
        if all(_is_numeric_value(v) for v in col_values):
            numeric_cols.add(col)

    html_parts = ['<table class="centered-table">']
    html_parts.append("<thead><tr>")
    for col in columns:
        html_parts.append(f"<th>{col}</th>")
    html_parts.append("</tr></thead>")
    html_parts.append("<tbody>")
    for _, row in df.iterrows():
        html_parts.append("<tr>")
        for col in columns:
            value = row[col]
            if value is None or (isinstance(value, float) and pd.isna(value)):
                value = ""
            if col in numeric_cols:
                if isinstance(value, float) and value == int(value):
                    value = f"{int(value):,}"
                elif isinstance(value, (int, float)):
                    value = f"{value:,}"
                html_parts.append(f'<td class="num-cell">{value}</td>')
            else:
                html_parts.append(f"<td>{value}</td>")
        html_parts.append("</tr>")
    html_parts.append("</tbody></table>")
    css = """
    <style>
    .centered-table { width: 100%; border-collapse: collapse; font-family: "Microsoft YaHei", "微软雅黑", sans-serif; margin: 10px 0; }
    .centered-table th, .centered-table td { border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; }
    .centered-table th { background-color: #f3f4f6; font-weight: 600; text-align: center; }
    .centered-table .num-cell { text-align: center; font-variant-numeric: tabular-nums; }
    .centered-table tbody tr:nth-child(even) { background-color: #fafafa; }
    .centered-table tbody tr:hover { background-color: #f0f9ff; }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)
    st.markdown("".join(html_parts), unsafe_allow_html=True)


def render_resource_detail(task):
    st.markdown("### 🔧 工序资源配置详情")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.info(f"**工序编号**\n\n{task.get('task_id', '暂无')}")
    with col2:
        st.info(f"**工序名称**\n\n{task.get('task_name', '暂无')}")
    with col3:
        st.info(f"**开始日期**\n\n{task.get('start_date', '暂无')}")
    with col4:
        st.info(f"**工期**\n\n{task.get('duration_days', 0)} 天")
    st.markdown("#### 资源配置")
    resources = task.get("assigned_resources", {})
    if isinstance(resources, dict) and resources:
        resource_data = [{"资源类型": k, "数量": v} for k, v in resources.items()]
        _render_centered_table(pd.DataFrame(resource_data))
    else:
        st.warning("该工序暂无资源配置信息")


def render_resource_plan(resource_plan):
    st.markdown("### 📦 资源计划概览")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("总人工工日", f"{resource_plan.get('total_manpower_days', 0):,.0f} 工日")
    with col2:
        st.metric("峰值人力", f"{resource_plan.get('peak_manpower', 0)} 人")
    equipment_peak = resource_plan.get("equipment_peak", {})
    if equipment_peak:
        st.markdown("#### 主要设备峰值")
        _render_centered_table(pd.DataFrame([{"设备名称": k, "峰值数量": v} for k, v in equipment_peak.items()]))
    material_summary = resource_plan.get("material_summary", [])
    if material_summary:
        st.markdown("#### 主要材料汇总")
        df_materials = pd.DataFrame(material_summary)
        df_materials.columns = ["材料名称", "总数量", "单位"]
        _render_centered_table(df_materials)


def render_risks(risks):
    st.markdown("### ⚠️ 风险与应对措施")
    for i, risk in enumerate(risks, 1):
        with st.expander(f"风险 {i}：{risk.get('risk_name', '未知风险')}"):
            st.markdown(f"**应对措施**：{risk.get('mitigation', '暂无措施')}")


def render_milestones_table(milestones):
    st.markdown("### 🏁 关键里程碑")
    if milestones:
        df_milestones = pd.DataFrame(milestones)
        df_milestones["date"] = pd.to_datetime(df_milestones["date"])
        df_milestones = df_milestones.sort_values("date")
        df_milestones["date"] = df_milestones["date"].dt.strftime("%Y-%m-%d")
        df_milestones.columns = ["里程碑名称", "日期", "关联工序", "描述"]
        _render_centered_table(df_milestones)


# ==================== 导出 ====================

def export_combined_html(fig_gantt, fig_manpower, progress_bar=None):
    def update_progress(step, total, msg):
        if progress_bar:
            try:
                progress_bar.progress(step / total, text=msg)
            except TypeError:
                progress_bar.progress(step / total)
    try:
        update_progress(1, 3, "正在渲染甘特图...")
        gantt_html = pio.to_html(fig_gantt, full_html=False, include_plotlyjs=False)
        update_progress(2, 3, "正在渲染资源曲线...")
        manpower_html = pio.to_html(fig_manpower, full_html=False, include_plotlyjs=False)

        combined_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>施工进度计划图</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body {{ margin: 0; padding: 20px; background: #fff; font-family: Microsoft YaHei, sans-serif; }}
        .chart-container {{ width: 100%; max-width: 1600px; margin: 0 auto; }}
        .gantt-section {{ width: 100%; height: 1000px; }}
        .manpower-section {{ width: 100%; height: 500px; margin-top: 20px; }}
        h1 {{ text-align: center; color: #333; }}
    </style>
</head>
<body>
    <h1>施工进度计划图</h1>
    <div class="chart-container">
        <div class="gantt-section">{gantt_html}</div>
        <div class="manpower-section">{manpower_html}</div>
    </div>
</body>
</html>
"""
        update_progress(3, 3, "正在保存...")
        return combined_html.encode("utf-8")
    except Exception as e:
        if progress_bar:
            try:
                progress_bar.progress(0, text=f"失败：{str(e)}")
            except TypeError:
                progress_bar.progress(0)
        st.error(f"导出失败：{str(e)}")
        return None


def export_tasks_csv(tasks_df):
    export_df = tasks_df.copy()
    export_df["start_date"] = export_df["start_date"].dt.strftime("%Y-%m-%d")
    export_df["finish_date"] = export_df["finish_date"].dt.strftime("%Y-%m-%d")
    export_df["assigned_resources"] = export_df["assigned_resources"].apply(
        lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)
    )
    export_df.columns = ["工序编号", "工序名称", "开始日期", "完成日期", "工期(天)", "资源配置", "是否关键工序", "分部编码"]
    return export_df.to_csv(index=False, encoding="utf-8-sig")


# ==================== 完整渲染 ====================

def render_plan_full(data, current_version="default"):
    """完整渲染函数：使用标签页拆分。"""
    try:
        structured = data["structured_output"]
        overview = structured["overview"]
        all_tasks = structured["all_tasks_schedule"]
        critical_tasks = structured.get("critical_path_tasks", [])
        milestones = structured.get("key_milestones", [])
        resource_plan = structured.get("resource_plan", {})
        risks = structured.get("risks", [])

        critical_ids = get_critical_task_ids(critical_tasks)
        tasks_df = tasks_to_dataframe(all_tasks, critical_ids)
        section_mapping = get_section_mapping(all_tasks)

        render_project_overview(overview)
        st.markdown("---")

        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "📊 甘特图", "📈 资源曲线", "🔍 工序详情", "🏁 里程碑", "⚠️ 风险",
        ])

        with tab1:
            col_filter1, col_filter2, col_filter3 = st.columns([2, 1, 1])
            with col_filter1:
                section_options = list(section_mapping.keys())
                selected_sections = st.multiselect(
                    "按分部工程筛选",
                    options=section_options,
                    format_func=lambda x: f"{x} - {section_mapping.get(x, '')}",
                    default=[],
                    help="功能：按施工分部工程（如施工准备、地基与基础、主体结构等）筛选甘特图中显示的工序。操作方法：点击下拉框选择一个或多个分部，图表将只显示所选分部的工序；不选则显示全部。",
                    key=f"section_filter_{current_version}",
                )
            with col_filter2:
                show_milestones = st.checkbox(
                    "显示里程碑",
                    value=True,
                    help="功能：在甘特图中用黄色菱形标记关键里程碑节点。操作方法：勾选后图表会自动刷新显示里程碑。",
                    key=f"show_milestones_{current_version}",
                )
            with col_filter3:
                show_resource_curve_tab = st.checkbox(
                    "同步显示资源曲线",
                    value=False,
                    help="功能：在甘特图标签页下方同步展示资源负荷曲线。操作方法：勾选后无需切换到资源曲线标签页即可查看。",
                    key=f"show_res_tab_{current_version}",
                )

            fig_gantt = create_gantt_chart(
                tasks_df,
                milestones,
                section_filter=selected_sections if selected_sections else None,
                show_milestones=show_milestones,
            )
            st.plotly_chart(fig_gantt, use_container_width=True, key=f"gantt_{current_version}")

            col_export1, col_export2 = st.columns([1, 1])
            with col_export1:
                html_key = f"html_export_{current_version}"
                if html_key not in st.session_state:
                    st.session_state[html_key] = None
                if st.session_state[html_key] is None:
                    if st.button(
                        "🌐 生成网页",
                        help="功能：将当前甘特图和资源曲线打包生成一个独立的 HTML 网页文件。操作方法：点击后等待生成完成，出现下载按钮即可下载。",
                        key=f"btn_html_{current_version}",
                    ):
                        try:
                            progress_bar = st.progress(0, text="正在准备...")
                        except TypeError:
                            progress_bar = st.progress(0)
                        filtered_tasks = tasks_df[tasks_df["section_code"].isin(selected_sections)].copy() if selected_sections else tasks_df.copy()
                        fig_manpower_for_export = create_manpower_curve(filtered_tasks)
                        result = export_combined_html(fig_gantt, fig_manpower_for_export, progress_bar)
                        if result:
                            st.session_state[html_key] = result
                            st.success("网页生成成功！")
                            st.rerun()
                else:
                    st.download_button(
                        label="📥 下载网页",
                        data=st.session_state[html_key],
                        file_name=f"{overview.get('project_name', '进度计划')}_进度图.html",
                        mime="text/html",
                        help="功能：下载已生成的 HTML 网页文件。操作方法：点击后浏览器会自动下载 .html 文件，可用浏览器打开查看。",
                        key=f"dl_html_{current_version}",
                    )
            with col_export2:
                csv_data = export_tasks_csv(tasks_df)
                st.download_button(
                    label="📊 导出工序表",
                    data=csv_data,
                    file_name=f"{overview.get('project_name', '进度计划')}_工序表.csv",
                    mime="text/csv",
                    help="功能：导出当前进度计划的所有工序信息为 CSV 表格。操作方法：点击后浏览器会自动下载 .csv 文件，可用 Excel 打开。",
                    key=f"dl_csv_{current_version}",
                )

            if show_resource_curve_tab:
                st.markdown("---")
                st.subheader("📈 资源负荷曲线")
                filtered_tasks = tasks_df[tasks_df["section_code"].isin(selected_sections)].copy() if selected_sections else tasks_df.copy()
                fig_manpower = create_manpower_curve(filtered_tasks)
                st.plotly_chart(fig_manpower, use_container_width=True, key=f"manpower_sync_{current_version}")

        with tab2:
            st.info("显示每日人力需求变化趋势，悬停可查看每日资源分配详情")
            filtered_tasks = tasks_df.copy()
            fig_manpower = create_manpower_curve(filtered_tasks)
            st.plotly_chart(fig_manpower, use_container_width=True, key=f"manpower_{current_version}")

            st.markdown("---")
            render_resource_plan(resource_plan)

        with tab3:
            st.info("查询各工序的资源配置、工期等详细信息")
            task_options = [f"{t['task_id']} - {t['task_name']}" for t in all_tasks]
            selected_task_label = st.selectbox(
                "选择工序查看资源配置详情",
                options=task_options,
                index=0,
                help="功能：从所有工序中选择一个，查看其详细的资源配置（工种、人数、设备等）。操作方法：点击下拉框选择工序，下方会自动显示该工序的详细信息。",
                key=f"task_select_{current_version}",
            )
            selected_task_id = selected_task_label.split(" - ")[0]
            selected_task = next((t for t in all_tasks if t["task_id"] == selected_task_id), None)
            if selected_task:
                render_resource_detail(selected_task)

            st.markdown("---")
            st.subheader("📋 全部工序一览")
            display_df = tasks_df[["task_id", "task_name", "start_date", "finish_date", "duration_days", "is_critical"]].copy()
            display_df["start_date"] = display_df["start_date"].dt.strftime("%Y-%m-%d")
            display_df["finish_date"] = display_df["finish_date"].dt.strftime("%Y-%m-%d")
            display_df["is_critical"] = display_df["is_critical"].apply(lambda x: "✅ 是" if x else "")
            display_df.columns = ["工序编号", "工序名称", "开始日期", "完成日期", "工期(天)", "关键工序"]
            _render_centered_table(display_df)

        with tab4:
            st.info("关键节点和重要时间点的汇总")
            render_milestones_table(milestones)

        with tab5:
            st.info("显示项目可能面临的风险及对应的应对措施")
            if risks:
                render_risks(risks)
            else:
                st.info("当前项目暂无风险记录")

        return tasks_df, overview
    except Exception as e:
        st.error(f"渲染失败：{str(e)}")
        st.exception(e)
        return None, None


# ==================== IFRAME 聊天框模块 ====================

def render_chat_panel():
    """渲染右侧 AI 聊天框（IFRAME 嵌入）。"""
    st.markdown("""
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 12px 16px; border-radius: 8px; color: white; margin-bottom: 8px;">
        <h4 style="color: white; margin: 0;">🤖 AI 智能对话</h4>
        <p style="margin: 4px 0 0 0; opacity: 0.9; font-size: 0.85rem;">由 Dify 提供：在下方输入框中描述项目需求 → AI 返回 JSON 文本</p>
    </div>
    """, unsafe_allow_html=True)
    iframe_html = f"""
    <iframe
        src="{IFRAME_URL}"
        style="width: 100%; height: 780px; min-height: 700px; border: 1px solid #e5e7eb; border-radius: 8px;"
        frameborder="0"
        allow="microphone">
    </iframe>
    """
    st.markdown(iframe_html, unsafe_allow_html=True)


# ==================== 顶部全局说明横幅 ====================

def render_workflow_banner():
    """展示两模块的工作流衔接说明。"""
    st.markdown("""
    <div style="background: #eef2ff; border-left: 4px solid #4f46e5; padding: 14px 18px; border-radius: 6px; margin-bottom: 14px;">
        <div style="font-weight: 600; color: #3730a3; margin-bottom: 6px;">📌 使用流程</div>
        <div style="color: #374151; font-size: 0.92rem; line-height: 1.7;">
            <b>模块一 · AI 对话</b>（右侧聊天框）：在右侧面板的输入框中向 AI 描述项目需求，AI 会返回一段 <b>JSON 文本</b>。<br>
            <b>模块二 · 进度计划展示</b>（左侧/下方）：将 AI 返回的 JSON 文本<b>保存为 <code>.json</code> 文件</b>，再通过左侧上传区传入本应用，即可自动渲染甘特图、资源曲线、工序详情等图表。<br>
            ⚠️ <b>需要进一步展示进度计划？请将 JSON 文本保存为 JSON 文件后传入左侧的上传区域。</b>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ==================== 主应用 ====================

def main():
    st.set_page_config(page_title="进度计划智能助手", page_icon="🏗️", layout="wide")

    st.markdown("""
    <style>
        .main .block-container { padding-top: 0.5rem; padding-bottom: 0.5rem; max-width: 100%; }
        [data-testid="stMetricValue"] { font-size: 1.1rem; }
        h1, h2, h3 { font-family: "Microsoft YaHei", "微软雅黑", sans-serif; }
        .stChatMessage { padding: 0.5rem 0; }
        /* 文件上传组件中文化（只作用于拖放区，不影响已选文件列表） */
        [data-testid="stFileUploaderDropzone"] > div:first-child { color: rgba(49, 51, 63, 0.8); font-size: 1rem; line-height: 1.4; text-align: center; }
        [data-testid="stFileUploaderDropzone"] > div:first-child > div { display: none; }
        [data-testid="stFileUploaderDropzone"] > div:first-child > button { display: none; }
        [data-testid="stFileUploaderDropzone"] > div:first-child::before { content: "将文件拖放到此处"; color: rgba(49, 51, 63, 0.8); display: block; font-size: 1rem; line-height: 1.4; }
        [data-testid="stFileUploaderDropzone"] > div:first-child::after { content: "限制：每个文件200MB"; color: rgba(49, 51, 63, 0.6); display: block; font-size: 0.8em; margin-top: 4px; }
        .stTabs [data-baseweb="tab-list"] { gap: 4px; }
        .stTabs [data-baseweb="tab"] { padding: 8px 16px; font-size: 14px; }
    </style>
    """, unsafe_allow_html=True)

    # 顶部标题
    st.markdown("""
    <div style="display: flex; align-items: center; gap: 15px; margin-bottom: 10px;">
        <svg width="56" height="56" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
            <circle cx="50" cy="50" r="46" fill="url(#grad1)" stroke="#2563eb" stroke-width="2"/>
            <defs>
                <linearGradient id="grad1" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" style="stop-color:#3b82f6;stop-opacity:1" />
                    <stop offset="100%" style="stop-color:#1e40af;stop-opacity:1" />
                </linearGradient>
            </defs>
            <rect x="22" y="35" width="56" height="45" fill="white" rx="3" opacity="0.95"/>
            <rect x="28" y="50" width="10" height="15" fill="#3b82f6" rx="1"/>
            <rect x="42" y="45" width="10" height="20" fill="#3b82f6" rx="1"/>
            <rect x="56" y="40" width="10" height="25" fill="#3b82f6" rx="1"/>
            <line x1="70" y1="35" x2="70" y2="18" stroke="white" stroke-width="2.5" stroke-linecap="round"/>
            <line x1="70" y1="20" x2="85" y2="20" stroke="white" stroke-width="2" stroke-linecap="round"/>
            <line x1="82" y1="20" x2="82" y2="26" stroke="white" stroke-width="1.5"/>
            <circle cx="82" cy="27" r="1.5" fill="#fbbf24"/>
        </svg>
        <div>
            <h1 style="margin: 0; font-size: 1.7rem; color: #1e3a8a;">进度计划智能助手</h1>
            <p style="margin: 2px 0 0 0; color: #64748b; font-size: 0.9rem;">AI 对话 + 进度计划可视化</p>
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("---")

    # 初始化 session_state
    if "data_versions" not in st.session_state:
        st.session_state.data_versions = {}
    if "current_version" not in st.session_state:
        st.session_state.current_version = None
    if "history_page" not in st.session_state:
        st.session_state.history_page = 0
    if "demo_loaded" not in st.session_state:
        st.session_state.demo_loaded = False
    if "upload_status" not in st.session_state:
        st.session_state.upload_status = None  # (type, msg)

    # 工作流说明
    render_workflow_banner()

    # ==================== 主体：左历史文件 + 右聊天框 ====================
    left_col, right_col = st.columns([2, 3], gap="large")

    with left_col:
        # ===== 上传区 =====
        st.markdown("""
        <div style="background: #f0f9ff; border-left: 4px solid #0284c7; padding: 12px 16px; border-radius: 6px; margin-bottom: 10px;">
            <div style="font-weight: 600; color: #075985; margin-bottom: 4px;">📤 进度计划 JSON 文件上传</div>
            <div style="color: #374151; font-size: 0.85rem; line-height: 1.6;">
                <b>功能</b>：上传由右侧 AI 聊天框返回的 JSON 文本所保存的 <code>.json</code> 文件，系统将自动渲染甘特图、资源曲线等图表。<br>
                <b>操作方法</b>：点击"Browse files"按钮或直接将 .json 文件拖入下方区域，松开即自动上传。<br>
                <b>规则</b>：同名文件按规则视为同一文件，<b>不会重复保存</b>，传入同名文件会提示失败。如需更新内容，请更换文件名后重新上传。
            </div>
        </div>
        """, unsafe_allow_html=True)

        uploaded_file = st.file_uploader(
            "上传JSON文件",
            type=["json"],
            help="点击选择文件或将 .json 文件拖入此区域。文件必须是 AI 聊天框返回的进度计划 JSON 格式。",
            key="file_uploader",
        )

        if uploaded_file is not None:
            # 先校验是否同名
            if check_history_file_exists(uploaded_file.name):
                st.session_state.upload_status = ("error", f"❌ 上传失败：历史中已存在同名文件 '{uploaded_file.name}'，按规则视为同一文件，不重复保存。请使用其他名称的文件。")
                st.error(st.session_state.upload_status[1])
            else:
                try:
                    file_content = uploaded_file.read()
                    data = load_json_from_upload(file_content)
                    is_valid, msg = validate_data_structure(data)
                    if is_valid:
                        wrapped = normalize_to_wrapped(data)
                        ok, info = save_file_unique(uploaded_file.name, file_content)
                        if ok:
                            version_name = uploaded_file.name.replace(".json", "")
                            st.session_state.data_versions[version_name] = wrapped
                            st.session_state.current_version = version_name
                            st.session_state.history_page = 0
                            st.session_state.upload_status = ("success", f"✅ 上传成功：'{uploaded_file.name}'")
                            st.success(st.session_state.upload_status[1])
                            st.rerun()
                        else:
                            st.session_state.upload_status = ("error", f"❌ {info}")
                            st.error(st.session_state.upload_status[1])
                    else:
                        st.session_state.upload_status = ("error", f"❌ 数据格式错误：{msg}")
                        st.error(st.session_state.upload_status[1])
                except Exception as e:
                    st.session_state.upload_status = ("error", f"❌ 解析失败：{str(e)}")
                    st.error(st.session_state.upload_status[1])

        # ===== 历史文件区 =====
        st.markdown("---")
        hist_title_col, hist_refresh_col = st.columns([4, 1])
        with hist_title_col:
            st.subheader("📂 历史文件")
        with hist_refresh_col:
            if st.button("🔄", key="refresh_history", help="功能：刷新左侧历史文件列表。操作方法：点击后重新读取本地存储目录中的所有 JSON 文件。"):
                st.session_state.history_page = 0
                st.rerun()
        history_files = get_history_json_files()

        if history_files:
            st.caption(f"共 {len(history_files)} 个文件（同名不重复保存）")

            page_size = 5
            total_pages = max(1, (len(history_files) + page_size - 1) // page_size)
            current_page = st.session_state.history_page
            start_idx = current_page * page_size
            end_idx = min(start_idx + page_size, len(history_files))
            page_files = history_files[start_idx:end_idx]

            if total_pages > 1:
                col_p1, col_p2, col_p3 = st.columns([1, 2, 1])
                with col_p1:
                    if st.button("◀", disabled=(current_page == 0), key="prev_page", help="功能：查看上一页历史文件。操作方法：点击后显示前 5 个历史文件。"):
                        st.session_state.history_page = max(0, current_page - 1)
                        st.rerun()
                with col_p2:
                    st.caption(f"第 {current_page + 1}/{total_pages} 页")
                with col_p3:
                    if st.button("▶", disabled=(current_page >= total_pages - 1), key="next_page", help="功能：查看下一页历史文件。操作方法：点击后显示后 5 个历史文件。"):
                        st.session_state.history_page = min(total_pages - 1, current_page + 1)
                        st.rerun()

            for hf in page_files:
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    st.markdown(f"**{hf['project_name']}**")
                    st.caption(f"📅 {hf['upload_time']}")
                with col2:
                    if st.button("加载", key=f"load_{hf['file_name']}", type="primary", help="功能：将该历史文件加载到当前会话并渲染图表。操作方法：点击后该计划的甘特图、资源曲线等图表会显示在页面下方。"):
                        try:
                            data = load_json_from_file(hf["file_path"])
                            is_valid, msg = validate_data_structure(data)
                            if is_valid:
                                wrapped = normalize_to_wrapped(data)
                                st.session_state.data_versions[hf["project_name"]] = wrapped
                                st.session_state.current_version = hf["project_name"]
                                st.success("✅ 已加载")
                                st.rerun()
                            else:
                                st.error(f"格式错误：{msg}")
                        except Exception as e:
                            st.error(f"加载失败：{str(e)}")
                with col3:
                    if st.button("🗑️", key=f"del_{hf['file_name']}", help="功能：永久删除该历史文件。操作方法：点击后文件将从本地存储中移除，不可恢复。"):
                        if delete_history_file(hf["file_path"]):
                            if hf["project_name"] in st.session_state.data_versions:
                                del st.session_state.data_versions[hf["project_name"]]
                            if st.session_state.current_version == hf["project_name"]:
                                remaining = list(st.session_state.data_versions.keys())
                                st.session_state.current_version = remaining[0] if remaining else None
                            st.success("已删除")
                            st.rerun()
        else:
            st.info("暂无历史文件")

    with right_col:
        render_chat_panel()

    # ==================== 参数规范与JSON格式规范（可折叠）====================
    st.markdown("---")
    st.markdown("""
    <div style="background: #f8fafc; border-left: 4px solid #64748b; padding: 10px 14px; border-radius: 6px; margin-bottom: 8px;">
        <div style="font-weight: 600; color: #475569;">📖 格式规范参考</div>
        <div style="color: #64748b; font-size: 0.85rem;">
            点击下方的折叠区域查看参数输入建议和 JSON 文件格式说明。这些规范仅供参考，帮助您更好地与 AI 沟通。
        </div>
    </div>
    """, unsafe_allow_html=True)

    with st.expander("📝 参数输入建议（点击展开）"):
        st.markdown("""
        <div style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 14px 18px; border-radius: 6px; margin-bottom: 14px;">
            <div style="font-weight: 600; color: #b45309; margin-bottom: 8px;">🤖 AI 智能识别说明</div>
            <div style="color: #78350f; font-size: 0.9rem; line-height: 1.7;">
                <b>AI 会自动识别您的输入内容</b>，您可以选择以下任意方式提交项目信息：
                <ul style="margin: 8px 0; padding-left: 20px;">
                    <li><b>方式一：聊天框直接输入</b> — 在右侧聊天框中用自然语言描述项目需求，无需严格遵循格式</li>
                    <li><b>方式二：上传 Word 文件</b> — 将项目信息整理成 Word 文档，通过聊天框的附件功能上传</li>
                    <li><b>方式三：混合输入</b> — 先上传 Word 文档，再在聊天框补充说明或调整需求</li>
                </ul>
                <b>下方参数列表仅为建议</b>，帮助您梳理项目信息，<b>不强求必须全部填写或按格式输入</b>。
            </div>
        </div>
        """, unsafe_allow_html=True)

        # 参数输入建议表格
        st.markdown("""
        <div style="font-weight: 600; color: #1e3a8a; margin-bottom: 12px;">📋 建议输入的项目参数（按模块分类）</div>
        """, unsafe_allow_html=True)

        param_data = {
            "模块": [
                "基本信息", "基本信息", "基本信息", "基本信息", "基本信息", "基本信息", "基本信息", "基本信息", "基本信息",
                "技术约束", "技术约束", "技术约束", "技术约束",
                "资源约束", "资源约束", "资源约束", "资源约束",
                "组织管理", "组织管理", "组织管理", "组织管理",
                "外部约束", "外部约束", "外部约束", "外部约束",
                "调整用例", "调整用例", "调整用例", "调整用例", "调整用例"
            ],
            "参数名称": [
                "工程名称", "建设地点", "总建筑面积", "占地面积", "建筑组成", "结构形式", "层数与高度", "总工期", "计划开工/竣工日期",
                "基坑支护工艺", "桩基工艺", "特殊工艺", "主体结构工艺",
                "劳动力峰值", "主要工种及人数", "主要设备", "主要材料",
                "总承包单位", "项目负责人", "技术负责人", "资金条件",
                "政府管制要求", "强制里程碑", "空间占用规则", "周边关系",
                "调整场景名称", "触发条件", "偏差参数", "影响范围", "调整目标"
            ],
            "说明": [
                "项目的名称", "项目所在地", "如：301354.26㎡", "如：50000㎡", "如：商业裙楼+住宅塔楼", "如：框架-剪力墙结构", "如：地上28层/地下2层", "如：365天", "如：2026年7月1日 至 2027年7月1日",
                "如：地下连续墙+内支撑", "如：钻孔灌注桩", "如：爬模、装配式施工", "如：铝模+爬架",
                "高峰期需要的工人数量", "如：钢筋工50人、木工80人", "如：塔吊3台、施工电梯2台", "如：钢筋5000吨、水泥3000吨",
                "承担总承包的单位名称", "姓名+资质等级", "姓名+职称", "如：按进度付款、预付款比例",
                "如：夜间施工限制、渣土运输时间", "如：封顶日期、竣工验收日期", "如：场地分区占用时段", "如：周边建筑保护要求",
                "如：暴雨导致桩基延迟", "如：连续降雨超过3天", "如：工期延误5天", "如：桩基工程及后续工序", "如：压缩后续工期"
            ]
        }
        df_params = pd.DataFrame(param_data)
        _render_centered_table(df_params)

        st.markdown("""
        <div style="background: #eff6ff; border-radius: 6px; padding: 12px 16px; margin-top: 14px;">
            <div style="font-weight: 600; color: #1e40af; margin-bottom: 6px;">💡 使用提示</div>
            <ul style="color: #1e3a8a; font-size: 0.88rem; margin: 0; padding-left: 20px; line-height: 1.8;">
                <li>您可以只提供<b>部分参数</b>，AI 会根据已有信息进行推理补全</li>
                <li>参数之间用<b>逗号、冒号、换行</b>分隔均可，AI 能自动识别</li>
                <li>如有<b>特殊要求</b>（如工期压缩、资源限制），请在输入中明确说明</li>
                <li>上传 Word 文件时，建议使用<b>清晰的标题和分段</b>，便于 AI 理解</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

    with st.expander("📋 JSON 文件格式说明（点击展开）"):
        st.markdown("""
        <div style="background: #f0fdf4; border-left: 4px solid #22c55e; padding: 12px 16px; border-radius: 6px; margin-bottom: 10px;">
            <div style="font-weight: 600; color: #15803d;">💡 关于 JSON 格式</div>
            <div style="color: #166534; font-size: 0.88rem; line-height: 1.7;">
                AI 返回的进度计划数据会以 <b>JSON 格式</b> 呈现。您只需要：
                <ol style="margin: 8px 0; padding-left: 20px;">
                    <li>将 AI 返回的 JSON 文本<b>复制</b></li>
                    <li>保存为 <code>.json</code> 文件（可用记事本或其他文本编辑器）</li>
                    <li>通过左侧上传区传入本应用</li>
                </ol>
                系统会自动渲染甘特图、资源曲线等图表。下方的格式说明供参考，<b>您无需手动编写 JSON</b>。
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("""
        <div style="font-weight: 600; color: #1e3a8a; margin-bottom: 12px;">📋 JSON 文件包含的主要字段</div>
        """, unsafe_allow_html=True)

        json_fields = {
            "字段路径": [
                "structured_output.overview",
                "structured_output.all_tasks_schedule",
                "structured_output.critical_path_tasks",
                "structured_output.key_milestones",
                "structured_output.resource_plan",
                "structured_output.risks"
            ],
            "含义": [
                "项目概览（名称、工期、起止日期等）",
                "所有工序的详细安排（编号、名称、日期、资源）",
                "关键路径上的工序列表",
                "关键里程碑节点",
                "资源计划汇总",
                "风险识别与应对措施"
            ],
            "是否必需": [
                "✅ 必需",
                "✅ 必需",
                "可选",
                "可选",
                "可选",
                "可选"
            ]
        }
        df_json = pd.DataFrame(json_fields)
        _render_centered_table(df_json)

    # ==================== 下方：示范项目（名创优品）+ 用户加载的图表 ====================
    st.markdown("---")
    
    # 自动加载名创优品示范项目
    demo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "名创优品施工进度计划.json")
    if os.path.exists(demo_path):
        if "demo_data" not in st.session_state:
            try:
                demo_data_raw = load_json_from_file(demo_path)
                is_valid, msg = validate_data_structure(demo_data_raw)
                if is_valid:
                    st.session_state.demo_data = normalize_to_wrapped(demo_data_raw)
                else:
                    st.warning(f"示范文件验证失败：{msg}")
            except Exception as e:
                st.warning(f"加载示范文件失败：{str(e)}")
        
        if "demo_data" in st.session_state and st.session_state.demo_data:
            st.markdown("""
            <div style="background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%); border-left: 4px solid #0ea5e9; padding: 14px 18px; border-radius: 6px;">
                <div style="font-weight: 600; color: #0369a1; font-size: 1.1rem;">🏗️ 示范项目：名创优品施工进度计划</div>
                <div style="color: #075985; font-size: 0.9rem; margin-top: 4px;">
                    下方展示名创优品项目的完整进度计划图表，用于演示本网页的各项功能。<br>
                    您也可以上传自己的 JSON 文件查看其他项目的进度计划。
                </div>
            </div>
            """, unsafe_allow_html=True)
            render_plan_full(st.session_state.demo_data, current_version="demo")

    # 如果用户加载了其他文件，也展示
    if st.session_state.current_version and st.session_state.current_version in st.session_state.data_versions:
        if st.session_state.current_version != "demo":
            st.markdown("---")
            st.markdown(f"""
            <div style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 12px 16px; border-radius: 6px; margin-bottom: 10px;">
                <div style="font-weight: 600; color: #b45309;">📊 已加载的进度计划：{st.session_state.current_version}</div>
            </div>
            """, unsafe_allow_html=True)
            current_data = st.session_state.data_versions[st.session_state.current_version]
            render_plan_full(current_data, current_version=st.session_state.current_version)
    elif not os.path.exists(demo_path):
        st.info("请通过左侧上传区传入 JSON 进度计划文件，或从历史文件列表加载")

    # ==================== 开发者印记 ====================
    st.markdown("---")
    st.markdown("""
    <div style="text-align: center; padding: 20px 0; color: #94a3b8; font-size: 0.85rem;">
        <div style="font-weight: 600; color: #64748b; margin-bottom: 4px;">🏗️ 智建领航</div>
        <div>华南理工大学 · 进度计划智能助手</div>
        <div style="margin-top: 4px;">Powered by Dify AI + Streamlit + Plotly</div>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()

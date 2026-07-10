"""
施工进度计划可视化网页应用
基于 Streamlit + Plotly 的通用数据驱动渲染器
支持甘特图、关键线路高亮、资源联动、多版本对比、数据导出等功能
"""

import json
import os
import io
import functools
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots


# ==================== 数据加载与解析模块 ====================

@st.cache_data
def load_json_from_file(file_path):
    """
    从本地文件加载JSON数据并缓存
    
    参数:
        file_path: JSON文件路径
        
    返回:
        解析后的结构化数据字典
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data


@st.cache_data
def load_json_from_upload(uploaded_file_content):
    """
    从上传的文件内容加载JSON数据并缓存
    
    参数:
        uploaded_file_content: 上传文件的字节内容
        
    返回:
        解析后的结构化数据字典
    """
    data = json.loads(uploaded_file_content.decode('utf-8'))
    return data


def validate_data_structure(data):
    """
    验证JSON数据结构是否符合规范
    
    参数:
        data: 待验证的数据字典
        
    返回:
        (is_valid, error_message) 元组
    """
    if "structured_output" not in data:
        return False, "数据格式错误：缺少 'structured_output' 字段"
    
    structured = data["structured_output"]
    
    # 检查必需字段
    required_fields = ["overview", "all_tasks_schedule"]
    for field in required_fields:
        if field not in structured:
            return False, f"数据格式错误：缺少 '{field}' 字段"
    
    # 检查overview字段
    overview = structured["overview"]
    overview_required = ["project_name", "total_duration_days", 
                         "planned_start_date", "planned_end_date"]
    for field in overview_required:
        if field not in overview:
            return False, f"数据格式错误：overview 缺少 '{field}' 字段"
    
    # 检查任务数据格式
    tasks = structured["all_tasks_schedule"]
    if not isinstance(tasks, list) or len(tasks) == 0:
        return False, "数据格式错误：all_tasks_schedule 为空或不是列表"
    
    task_required = ["task_id", "task_name", "start_date", "finish_date", "duration_days"]
    for i, task in enumerate(tasks):
        for field in task_required:
            if field not in task:
                return False, f"数据格式错误：第 {i+1} 个任务缺少 '{field}' 字段"
    
    return True, "数据格式验证通过"


def get_local_json_files(directory):
    """
    获取指定目录下的所有JSON文件
    
    参数:
        directory: 目录路径
        
    返回:
        JSON文件名列表
    """
    if not os.path.exists(directory):
        return []
    return [f for f in os.listdir(directory) if f.endswith('.json')]


def get_history_directory():
    """
    获取历史文件存储目录路径
    
    返回:
        历史文件目录路径
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    history_dir = os.path.join(base_dir, "uploaded_history")
    if not os.path.exists(history_dir):
        os.makedirs(history_dir)
    return history_dir


def save_uploaded_file_to_history(file_name, file_content):
    """
    将上传的文件保存到历史目录（按文件名唯一存储）
    同一文件名会被覆盖，不使用时间戳后缀
    
    参数:
        file_name: 文件名
        file_content: 文件字节内容
        
    返回:
        保存后的文件路径
    """
    history_dir = get_history_directory()
    # 统一使用原始文件名，不加时间戳
    if not file_name.endswith('.json'):
        file_name = file_name + '.json'
    save_path = os.path.join(history_dir, file_name)
    
    with open(save_path, 'wb') as f:
        f.write(file_content)
    
    return save_path


def check_history_file_exists(file_name):
    """
    检查历史文件中是否已存在该文件名
    
    参数:
        file_name: 文件名
        
    返回:
        bool - 是否存在
    """
    history_dir = get_history_directory()
    if not file_name.endswith('.json'):
        file_name = file_name + '.json'
    save_path = os.path.join(history_dir, file_name)
    return os.path.exists(save_path)


def get_history_json_files():
    """
    获取历史目录下的所有JSON文件
    
    返回:
        [{'file_name', 'file_path', 'project_name', 'upload_time'}] 列表
    """
    history_dir = get_history_directory()
    if not os.path.exists(history_dir):
        return []
    
    files = []
    for f in os.listdir(history_dir):
        if f.endswith('.json'):
            file_path = os.path.join(history_dir, f)
            # 获取文件修改时间
            try:
                mtime = os.path.getmtime(file_path)
                upload_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                upload_time = "未知"
            
            project_name = f[:-5] if f.endswith('.json') else f  # 去掉.json后缀
            files.append({
                'file_name': f,
                'file_path': file_path,
                'project_name': project_name,
                'upload_time': upload_time
            })
    
    # 按文件名排序
    files.sort(key=lambda x: x['file_name'])
    return files


def delete_history_file(file_path):
    """
    删除历史文件
    
    参数:
        file_path: 文件路径
        
    返回:
        是否成功删除
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False
    except Exception:
        return False


# ==================== 数据处理模块 ====================

def extract_section_from_task_id(task_id):
    """
    从task_id中提取分部工程编码（第一段数字）
    
    参数:
        task_id: 任务ID，如 "1.1.1"
        
    返回:
        分部编码，如 "1"
    """
    parts = task_id.split('.')
    if parts:
        return parts[0]
    return "0"


def get_section_mapping(tasks):
    """
    根据任务列表生成分部工程映射
    
    参数:
        tasks: 任务列表
        
    返回:
        {分部编码: 分部名称} 字典
    """
    sections = {}
    section_names = {
        "1": "施工准备",
        "2": "地基与基础",
        "3": "主体结构",
        "4": "建筑装饰装修",
        "5": "建筑屋面",
        "6": "建筑给水排水",
        "7": "建筑电气",
        "8": "智能建筑",
        "9": "建筑节能与消防",
        "10": "室外工程",
        "11": "竣工验收"
    }
    
    for task in tasks:
        section_code = extract_section_from_task_id(task["task_id"])
        if section_code not in sections:
            # 使用预设名称或自动生成
            section_name = section_names.get(section_code, f"分部{section_code}")
            sections[section_code] = section_name
    
    return sections


def get_critical_task_ids(critical_path_tasks):
    """
    从关键路径任务列表中提取task_id集合
    
    参数:
        critical_path_tasks: 关键路径任务列表
        
    返回:
        关键任务ID的集合
    """
    return {task["task_id"] for task in critical_path_tasks}


def tasks_to_dataframe(tasks, critical_task_ids):
    """
    将任务列表转换为DataFrame，便于绘图
    
    参数:
        tasks: 任务列表
        critical_task_ids: 关键任务ID集合
        
    返回:
        pandas DataFrame
    """
    df = pd.DataFrame(tasks)
    
    # 添加是否关键任务标记
    df["is_critical"] = df["task_id"].isin(critical_task_ids)
    
    # 添加分部编码
    df["section_code"] = df["task_id"].apply(extract_section_from_task_id)
    
    # 转换日期格式
    df["start_date"] = pd.to_datetime(df["start_date"])
    df["finish_date"] = pd.to_datetime(df["finish_date"])
    
    # 按开始日期和task_id排序
    df = df.sort_values(["start_date", "task_id"]).reset_index(drop=True)
    
    return df


@st.cache_data
def calculate_daily_resources(tasks_df):
    """
    计算每日资源需求明细（已缓存，避免重复计算）。
    返回：日期列表、总人数列表、每日资源明细字符串列表
    """
    start_date = tasks_df["start_date"].min()
    end_date = tasks_df["finish_date"].max()
    date_range = pd.date_range(start=start_date, end=end_date, freq='D')

    # 总人力
    daily_manpower = pd.Series(0, index=date_range, dtype=float)
    # 每日资源明细: {日期: {资源名: 数量}}
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
            # 累加总人力
            daily_manpower.loc[active_dates] += count
            # 累加明细
            for d in active_dates:
                daily_detail[d][resource_name] = daily_detail[d].get(resource_name, 0) + count

    # 将明细字典格式化为字符串
    detail_texts = []
    for d in date_range:
        items = daily_detail[d]
        if items:
            lines = "<br>".join([f"  {k}: {v}" for k, v in sorted(items.items())])
            detail_texts.append(lines)
        else:
            detail_texts.append("无")

    return date_range, daily_manpower.tolist(), detail_texts


# ==================== 图表绘制模块 ====================

def _format_cn_date(dt):
    """把日期格式化为 '2026年12月1日' 形式"""
    try:
        return f"{dt.year}年{dt.month}月{dt.day}日"
    except Exception:
        return str(dt)


def _format_short_date(dt):
    """把日期格式化为 '12/1' 形式（去掉前导零）"""
    try:
        return f"{int(dt.month)}/{int(dt.day)}"
    except Exception:
        return str(dt)


@st.cache_data
def _build_gantt_data(tasks_df, section_filter=None):
    """
    构造甘特图绘图数据（已缓存，避免重复计算）。
    返回 DataFrame，每行含 label / Start / Finish / duration / row_type / resources / bar_color 等。
    """
    # 1. 筛选
    if section_filter and len(section_filter) > 0:
        filtered_df = tasks_df[tasks_df["section_code"].isin(section_filter)].copy()
    else:
        filtered_df = tasks_df.copy()

    if len(filtered_df) == 0:
        return None

    # 2. 准备工序数据（按 task_id 排序，保证从上到下从小到大）
    plot_df = filtered_df.copy()
    plot_df["Start"] = pd.to_datetime(plot_df["start_date"])
    plot_df["Finish"] = pd.to_datetime(plot_df["finish_date"])
    # 按 section_code 数值排序，再按 task_id 排序
    plot_df["_sec_num"] = plot_df["section_code"].astype(int)
    plot_df = plot_df.sort_values(["_sec_num", "task_id"]).reset_index(drop=True)

    # 3. 构造分部工程聚合
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

    # 4. 构造有序行
    ordered_rows = []
    for sec in sections:
        # 大类行
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
        # 子工序行
        for _, t in sec["children"].iterrows():
            resources = t.get("assigned_resources", {})
            # 小类永远红色，大类永远黑色
            color = "#e74c3c"
            ordered_rows.append({
                "label": f"  {t['task_id']} {t['task_name']}",
                "Start": t["Start"],
                "Finish": t["Finish"],
                "duration": t["duration_days"],
                "row_type": "task",
                "bar_color": color,
                "resources": resources if isinstance(resources, dict) else {},
                "task_id": t["task_id"],
                "task_name": t["task_name"],
            })

    return pd.DataFrame(ordered_rows)


@functools.lru_cache(maxsize=512)
def _build_resource_hover_text_cached(resources_tuple):
    """缓存版本：把 resources 元组格式化为 hover 文本"""
    if not resources_tuple:
        return "无资源配置"
    lines = [f"  {k}: {v}" for k, v in resources_tuple]
    return "<br>".join(lines)


def _build_resource_hover_text(resources):
    """把 resources 字典格式化为 hover 文本（带缓存）"""
    if not resources or not isinstance(resources, dict):
        return "无资源配置"
    return _build_resource_hover_text_cached(tuple(sorted(resources.items())))


def create_gantt_chart(tasks_df, milestones, section_filter=None, show_milestones=True):
    """
    创建甘特图：
    - 横道两色：黑色=分部大类/自成一类，红色=小类工序
    - 大类命名：#3#（含4道工序）
    - 工序从上到下按编号从小到大
    - 标题下方加一行时间轴，底部也有时间轴
    - 左侧：工序名 + 起止日期 + 工期
    - hover显示资源明细
    """
    rows_df = _build_gantt_data(tasks_df, section_filter=section_filter)
    if rows_df is None or len(rows_df) == 0:
        fig = go.Figure()
        fig.update_layout(title="施工进度甘特图（暂无数据）")
        return fig

    y_order = rows_df["label"].tolist()
    n_rows = len(rows_df)

    fig = go.Figure()

    # 1. 按颜色分组画横道（大类=黑，小类=红；自成一类=黑）
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

    # 2. 透明 Scatter trace：覆盖整个日期范围，解决悬停灵敏度问题
    #    hover 只展示当天正在进行的红色小类工序及其资源配置
    date_min = rows_df["Start"].min()
    date_max = rows_df["Finish"].max()
    all_dates = pd.date_range(start=date_min, end=date_max, freq='D')
    red_tasks_list = red_rows.to_dict('records')

    # 优化：预计算每个任务的hover文本，避免每天重复构建
    task_hover_cache = {}
    for t in red_tasks_list:
        res_text = _build_resource_hover_text(t["resources"])
        task_hover_cache[t["task_id"]] = (
            f"<b>{t['task_id']} {t['task_name']}</b><br>"
            f"工期：{t['duration']}天<br>"
            f"资源配置：{res_text}"
        )
    
    # 优化：使用日期区间展开，避免O(n*m)双重循环
    date_to_hover = {}
    for t in red_tasks_list:
        task_dates = pd.date_range(start=t["Start"], end=t["Finish"], freq='D')
        hover = task_hover_cache[t["task_id"]]
        for d in task_dates:
            d_key = d.strftime('%Y-%m-%d')
            if d_key not in date_to_hover:
                date_to_hover[d_key] = []
            date_to_hover[d_key].append(hover)
    
    daily_hover_texts = []
    for d in all_dates:
        d_key = d.strftime('%Y-%m-%d')
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

    # 3. 横道左右两端日期标签
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

    # 3. 左侧信息列（y轴标签）
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

    # 4. 里程碑
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

    # 5. 布局
    height = max(600, n_rows * 28 + 200)
    fig.update_layout(
        title=dict(
            text="施工进度甘特图",
            font=dict(size=18, family="Microsoft YaHei"),
            x=0.5, xanchor="center",
        ),
        barmode="overlay",
        height=height,
        margin=dict(l=80, r=80, t=100, b=100),
        plot_bgcolor="white",
        paper_bgcolor="white",
        annotations=annotations,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            font=dict(family="Microsoft YaHei"),
        ),
        hoverlabel=dict(
            font=dict(family="Microsoft YaHei", size=12),
            bgcolor="white", bordercolor="#ddd",
        ),
    )

    # y 轴
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

    # x 轴（底部）
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
    # 顶部时间轴（xaxis2，覆盖在x轴上）
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


def build_combined_gantt_and_manpower(
    tasks_df, milestones, section_filter=None, show_milestones=True
):
    """
    合并甘特图与人力曲线到一个 figure 中。
    上下两行子图，共享 X 轴；甘特图固定显示在最上方，
    不论是否显示资源曲线，工序顺序保持一致（按 task_id 从小到大）。
    """
    rows_df = _build_gantt_data(tasks_df, section_filter=section_filter)
    if rows_df is None or len(rows_df) == 0:
        fig = go.Figure()
        fig.update_layout(title="暂无数据")
        return fig

    y_order = rows_df["label"].tolist()
    n_rows = len(rows_df)

    # 1. 用 make_subplots 创建上下两图（共享 X 轴）
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.70, 0.30],
        vertical_spacing=0.12
    )

    # 2. 上图：横道（黑=大类/自成一类，红=小类）
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
        hover_texts = []
        for _, row in label_set.iterrows():
            if row["row_type"] == "section":
                hover_texts.append(
                    f"<b>{row['label']}</b><br>"
                    f"开始：{_format_cn_date(row['Start'])}<br>"
                    f"完成：{_format_cn_date(row['Finish'])}<br>"
                    f"工期：{row['duration']}天"
                )
            else:
                res_text = _build_resource_hover_text(row["resources"])
                hover_texts.append(
                    f"<b>{row['task_id']} {row['task_name']}</b><br>"
                    f"开始：{_format_cn_date(row['Start'])}<br>"
                    f"完成：{_format_cn_date(row['Finish'])}<br>"
                    f"工期：{row['duration']}天<br>"
                    f"<b>资源配置：</b><br>{res_text}"
                )

        fig.add_trace(go.Bar(
            x=x_durations,
            y=label_set["label"].tolist(),
            base=[d.to_pydatetime() for d in label_set["Start"]],
            orientation="h",
            marker=dict(color=color, line=dict(color="#333", width=0.5)),
            name=name,
            showlegend=True,
            hoverinfo="text",
            hovertext=hover_texts,
            width=0.6,
        ), row=1, col=1)

    # 3. 上图：左右两端日期标签
    annotations = []
    for _, row in rows_df.iterrows():
        start_dt = row["Start"].to_pydatetime()
        finish_dt = row["Finish"].to_pydatetime()
        annotations.append(dict(
            x=start_dt, y=row["label"],
            text=_format_short_date(start_dt),
            showarrow=False, xanchor="right", yanchor="middle",
            xshift=-5, xref="x", yref="y",
            font=dict(size=9, color="#333", family="Microsoft YaHei"),
        ))
        annotations.append(dict(
            x=finish_dt, y=row["label"],
            text=_format_short_date(finish_dt),
            showarrow=False, xanchor="left", yanchor="middle",
            xshift=5, xref="x", yref="y",
            font=dict(size=9, color="#333", family="Microsoft YaHei"),
        ))

    # 4. 左侧信息（y轴 ticktext）
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

    # 5. 上图：里程碑
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
            ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=12, color="#f39c12", symbol="diamond"),
            name="里程碑", showlegend=True,
        ), row=1, col=1)

    # 6. 下图：人力曲线
    date_range, daily_manpower, daily_detail_texts = calculate_daily_resources(
        tasks_df[tasks_df["section_code"].isin(section_filter)].copy() if section_filter
        else tasks_df.copy()
    )
    x_dates = [pd.Timestamp(d).to_pydatetime() for d in date_range]
    y_vals = [int(v) for v in daily_manpower]

    fig.add_trace(go.Scatter(
        x=x_dates, y=y_vals, mode="lines", fill="tozeroy",
        line=dict(color="#27ae60", width=2),
        fillcolor="rgba(39, 174, 96, 0.3)",
        name="人力需求",
        customdata=daily_detail_texts,
        hovertemplate=(
            "<b>日期：%{x|%Y-%m-%d}</b><br>"
            "总人力：%{y}人<br>"
            "<b>资源明细：</b><br>%{customdata}<extra></extra>"
        ),
    ), row=2, col=1)

    # 7. 整体布局
    height = max(800, n_rows * 28 + 400)
    fig.update_layout(
        title=dict(
            text="施工进度甘特图 + 资源负荷曲线",
            font=dict(size=18, family="Microsoft YaHei"),
            x=0.5, xanchor="center",
        ),
        barmode="overlay",
        height=height,
        margin=dict(l=80, r=80, t=100, b=80),
        plot_bgcolor="white",
        paper_bgcolor="white",
        annotations=annotations,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            font=dict(family="Microsoft YaHei"),
        ),
        hoverlabel=dict(
            font=dict(family="Microsoft YaHei", size=12),
            bgcolor="white", bordercolor="#ddd",
        ),
    )

    # 上图 y 轴
    fig.update_yaxes(
        categoryorder="array",
        categoryarray=y_order[::-1],
        ticktext=tick_texts[::-1],
        tickvals=y_order[::-1],
        tickfont=dict(size=10, family="Microsoft YaHei"),
        gridcolor="rgba(0,0,0,0.05)",
        showgrid=True, zeroline=False,
        row=1, col=1,
    )
    # 下图 y 轴（人力）
    fig.update_yaxes(
        title_text="人力（人）",
        gridcolor="rgba(0,0,0,0.1)",
        rangemode="tozero", zeroline=False,
        row=2, col=1,
    )
    # 上图 x 轴（底部）
    fig.update_layout(
        xaxis=dict(
            type="date",
            tickformat="%Y年%m月%d日",
            tickangle=-45,
            gridcolor="rgba(0,0,0,0.1)",
            showgrid=True,
            zeroline=False,
            side="bottom",
        ),
        # 上图顶部额外时间轴（xaxis3）
        xaxis3=dict(
            type="date",
            tickformat="%Y年%m月%d日",
            tickangle=-45,
            gridcolor="rgba(0,0,0,0)",
            showgrid=False,
            zeroline=False,
            side="top",
            overlaying="x",
            showticklabels=True,
            anchor="y",
        ),
        # 下图 x 轴（底部）
        xaxis2=dict(
            type="date",
            tickformat="%Y年%m月%d日",
            tickangle=-45,
            gridcolor="rgba(0,0,0,0.1)",
            showgrid=True,
            zeroline=False,
            side="bottom",
            title_text="日期",
        ),
    )

    return fig


def create_manpower_curve(tasks_df):
    """
    创建人力需求曲线图（含资源明细）

    参数:
        tasks_df: 任务DataFrame

    返回:
        plotly Figure对象
    """
    date_range, daily_manpower, daily_detail_texts = calculate_daily_resources(tasks_df)

    # 把 numpy datetime64 转成 python datetime
    x_dates = [pd.Timestamp(d).to_pydatetime() for d in date_range]
    y_vals = [int(v) for v in daily_manpower]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=x_dates,
        y=y_vals,
        mode="lines",
        fill="tozeroy",
        line=dict(color="#27ae60", width=2),
        fillcolor="rgba(39, 174, 96, 0.3)",
        name="人力需求",
        customdata=daily_detail_texts,
        hovertemplate="<b>日期：%{x|%Y-%m-%d}</b><br>总人力：%{y}人<br><b>资源明细：</b><br>%{customdata}<extra></extra>",
    ))

    fig.update_layout(
        title=dict(
            text="资源负荷曲线（人力）",
            font=dict(size=16, family="Microsoft YaHei"),
            x=0.5
        ),
        xaxis=dict(
            title="日期",
            type="date",
            tickformat="%Y-%m-%d",
            tickangle=-45,
            gridcolor="rgba(0,0,0,0.1)",
            showgrid=True
        ),
        yaxis=dict(
            title="人力（人）",
            gridcolor="rgba(0,0,0,0.1)",
            rangemode="tozero"
        ),
        height=400,
        margin=dict(l=60, r=30, t=60, b=80),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hoverlabel=dict(
            font=dict(family="Microsoft YaHei")
        ),
        showlegend=False
    )

    return fig


# ==================== 界面展示模块 ====================

def render_project_overview(overview):
    """
    渲染项目概览卡片
    
    参数:
        overview: 项目概览数据字典
    """
    st.markdown("### 📊 项目概览")
    
    # 项目名称单独一行，粗体放大
    st.markdown(f"<h2 style='font-weight: bold; color: #1e3a8a;'>{overview.get('project_name', '未知项目')}</h2>", unsafe_allow_html=True)
    
    # 其他信息在下面一行
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="总工期",
            value=f"{overview.get('total_duration_days', 0)} 天"
        )
    
    with col2:
        st.metric(
            label="计划开始",
            value=overview.get("planned_start_date", "N/A")
        )
    
    with col3:
        st.metric(
            label="计划完成",
            value=overview.get("planned_end_date", "N/A")
        )
    
    with col4:
        st.metric(
            label="关键路径工序数",
            value=f"{overview.get('critical_path_length', 0)} 项"
        )


def _is_numeric_value(v):
    """判断值是否为数字（int/float/可转为数字的字符串）"""
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
    """
    渲染带数字居中的HTML表格
    - 文本列左对齐
    - 数字列居中对齐
    """
    if df is None or len(df) == 0:
        st.info("暂无数据")
        return

    columns = df.columns.tolist()
    # 判断每列是否为数字列
    numeric_cols = set()
    for col in columns:
        col_values = df[col].dropna()
        if len(col_values) == 0:
            continue
        # 如果该列所有非空值都是数字，则视为数字列
        if all(_is_numeric_value(v) for v in col_values):
            numeric_cols.add(col)

    # 构建HTML表格
    html_parts = ['<table class="centered-table">']
    # 表头
    html_parts.append('<thead><tr>')
    for col in columns:
        html_parts.append(f'<th>{col}</th>')
    html_parts.append('</tr></thead>')
    # 表体
    html_parts.append('<tbody>')
    for _, row in df.iterrows():
        html_parts.append('<tr>')
        for col in columns:
            value = row[col]
            if value is None or (isinstance(value, float) and pd.isna(value)):
                value = ""
            if col in numeric_cols:
                # 数字列居中
                if isinstance(value, float) and value == int(value):
                    value = f"{int(value):,}"
                elif isinstance(value, (int, float)):
                    value = f"{value:,}"
                html_parts.append(f'<td class="num-cell">{value}</td>')
            else:
                html_parts.append(f'<td>{value}</td>')
        html_parts.append('</tr>')
    html_parts.append('</tbody></table>')

    # 添加CSS样式
    css = """
    <style>
    .centered-table {
        width: 100%;
        border-collapse: collapse;
        font-family: "Microsoft YaHei", "微软雅黑", sans-serif;
        margin: 10px 0;
    }
    .centered-table th, .centered-table td {
        border: 1px solid #e5e7eb;
        padding: 8px 12px;
        text-align: left;
    }
    .centered-table th {
        background-color: #f3f4f6;
        font-weight: 600;
        text-align: center;
    }
    .centered-table .num-cell {
        text-align: center;
        font-variant-numeric: tabular-nums;
    }
    .centered-table tbody tr:nth-child(even) {
        background-color: #fafafa;
    }
    .centered-table tbody tr:hover {
        background-color: #f0f9ff;
    }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)
    st.markdown("".join(html_parts), unsafe_allow_html=True)


def render_resource_detail(task):
    """
    渲染选中工序的资源详情
    
    参数:
        task: 任务数据字典
    """
    st.markdown("### 🔧 工序资源配置详情")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.info(f"**工序编号**\n\n{task.get('task_id', 'N/A')}")
    
    with col2:
        st.info(f"**工序名称**\n\n{task.get('task_name', 'N/A')}")
    
    with col3:
        st.info(f"**开始日期**\n\n{task.get('start_date', 'N/A')}")
    
    with col4:
        st.info(f"**工期**\n\n{task.get('duration_days', 0)} 天")
    
    st.markdown("#### 资源配置")
    
    resources = task.get("assigned_resources", {})
    if isinstance(resources, dict) and resources:
        # 创建资源表格
        resource_data = []
        for resource_name, quantity in resources.items():
            resource_data.append({
                "资源类型": resource_name,
                "数量": quantity
            })
        
        df_resources = pd.DataFrame(resource_data)
        _render_centered_table(df_resources)
    else:
        st.warning("该工序暂无资源配置信息")


def render_resource_plan(resource_plan):
    """
    渲染资源计划概览
    
    参数:
        resource_plan: 资源计划数据字典
    """
    st.markdown("### 📦 资源计划概览")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.metric(
            label="总人工工日",
            value=f"{resource_plan.get('total_manpower_days', 0):,.0f} 工日"
        )
    
    with col2:
        st.metric(
            label="峰值人力",
            value=f"{resource_plan.get('peak_manpower', 0)} 人"
        )
    
    # 设备峰值
    equipment_peak = resource_plan.get("equipment_peak", {})
    if equipment_peak:
        st.markdown("#### 主要设备峰值")
        eq_data = [{"设备名称": k, "峰值数量": v} for k, v in equipment_peak.items()]
        _render_centered_table(pd.DataFrame(eq_data))
    
    # 材料汇总
    material_summary = resource_plan.get("material_summary", [])
    if material_summary:
        st.markdown("#### 主要材料汇总")
        df_materials = pd.DataFrame(material_summary)
        # 表头改为中文
        df_materials.columns = ["材料名称", "总数量", "单位"]
        _render_centered_table(df_materials)


def render_risks(risks):
    """
    渲染风险列表
    
    参数:
        risks: 风险列表
    """
    st.markdown("### ⚠️ 风险与应对措施")
    
    for i, risk in enumerate(risks, 1):
        with st.expander(f"风险 {i}：{risk.get('risk_name', '未知风险')}"):
            st.markdown(f"**应对措施**：{risk.get('mitigation', '暂无措施')}")


def render_milestones_table(milestones):
    """
    渲染里程碑表格
    
    参数:
        milestones: 里程碑列表
    """
    st.markdown("### 🏁 关键里程碑")
    
    if milestones:
        df_milestones = pd.DataFrame(milestones)
        # 按日期排序
        df_milestones["date"] = pd.to_datetime(df_milestones["date"])
        df_milestones = df_milestones.sort_values("date")
        df_milestones["date"] = df_milestones["date"].dt.strftime("%Y-%m-%d")
        # 表头改为中文
        df_milestones.columns = ["里程碑名称", "日期", "关联工序", "描述"]
        _render_centered_table(df_milestones)


# ==================== 导出功能模块 ====================

def export_gantt_html(fig):
    """
    将甘特图导出为HTML文件（无需额外依赖，跨平台通用）
    """
    try:
        html_str = pio.to_html(
            fig,
            full_html=True,
            include_plotlyjs="cdn",
            default_width="100%",
            default_height="100%"
        )
        return html_str.encode('utf-8')
    except Exception as e:
        st.error(f"HTML导出失败：{str(e)}")
        return None


def export_combined_html(fig_gantt, fig_manpower, progress_bar=None):
    """
    将甘特图和资源曲线合并为一个HTML文件（上下排列，跨平台通用）
    progress_bar: Streamlit progress bar 用于显示进度
    """
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
        return combined_html.encode('utf-8')

    except Exception as e:
        if progress_bar:
            try:
                progress_bar.progress(0, text=f"失败：{str(e)}")
            except TypeError:
                progress_bar.progress(0)
        st.error(f"导出失败：{str(e)}")
        return None


def export_tasks_csv(tasks_df):
    """
    将工序数据导出为CSV
    
    参数:
        tasks_df: 任务DataFrame
        
    返回:
        CSV字符串
    """
    # 准备导出数据
    export_df = tasks_df.copy()
    export_df["start_date"] = export_df["start_date"].dt.strftime("%Y-%m-%d")
    export_df["finish_date"] = export_df["finish_date"].dt.strftime("%Y-%m-%d")
    
    # 将资源字典转换为字符串
    export_df["assigned_resources"] = export_df["assigned_resources"].apply(
        lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)
    )
    
    # 表头改为中文
    export_df.columns = ["工序编号", "工序名称", "开始日期", "完成日期", "工期(天)", "资源配置", "是否关键工序", "分部编码"]
    
    # 转换为CSV
    csv = export_df.to_csv(index=False, encoding='utf-8-sig')
    return csv


# ==================== 主应用 ====================

def main():
    """
    主应用函数
    """
    # 页面配置
    st.set_page_config(
        page_title="施工进度计划可视化看板",
        page_icon="🏗️",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # 自定义CSS样式
    st.markdown("""
    <style>
        .main .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
            max-width: 95%;
        }
        [data-testid="stMetricValue"] {
            font-size: 1.2rem;
        }
        h1, h2, h3 {
            font-family: "Microsoft YaHei", "微软雅黑", sans-serif;
        }
        /* 表格样式：数字单元格居中 */
        .stTable table {
            width: 100%;
            border-collapse: collapse;
        }
        .stTable th, .stTable td {
            border: 1px solid #e5e7eb;
            padding: 8px 12px;
            text-align: left;
        }
        .stTable th {
            background-color: #f3f4f6;
            font-weight: 600;
        }
        /* 使用CSS选择器使数字单元格居中 */
        .stTable td:has(> span:only-child) {
            text-align: center;
        }
        /* 针对纯数字内容的单元格 */
        .stTable td[data-align="center"] {
            text-align: center;
        }
    </style>
    """, unsafe_allow_html=True)
    
    # 页面标题
    st.title("🏗️ 施工进度计划可视化看板")
    st.markdown("---")
    
    # 侧边栏 - 数据加载
    with st.sidebar:
        st.header("📁 数据加载")
        
        # 数据版本管理（用于多版本对比）
        if "data_versions" not in st.session_state:
            st.session_state.data_versions = {}
        
        # 数据加载方式
        load_option = st.radio(
            "选择数据来源",
            ["本地JSON文件", "上传JSON文件"],
            index=0
        )
        
        loaded_data = None
        current_version_name = None
        
        if load_option == "本地JSON文件":
            # 获取当前目录下的JSON文件
            local_dir = os.path.dirname(os.path.abspath(__file__))
            json_files = get_local_json_files(local_dir)
            
            if json_files:
                selected_file = st.selectbox(
                    "选择JSON文件",
                    json_files,
                    index=0
                )
                
                if st.button("加载数据", type="primary"):
                    try:
                        file_path = os.path.join(local_dir, selected_file)
                        data = load_json_from_file(file_path)
                        is_valid, msg = validate_data_structure(data)
                        
                        if is_valid:
                            version_name = selected_file.replace('.json', '')
                            st.session_state.data_versions[version_name] = data
                            st.success(f"成功加载：{selected_file}")
                        else:
                            st.error(msg)
                    except Exception as e:
                        st.error(f"加载失败：{str(e)}")
            else:
                st.info("当前目录下未找到JSON文件，请上传文件")
        
        elif load_option == "上传JSON文件":
            uploaded_file = st.file_uploader(
                "上传JSON文件",
                type=["json"],
                help="选择符合格式要求的施工进度JSON文件（上传后将自动保存到本地）",
                key="file_uploader"
            )
            
            if uploaded_file is not None:
                # 检查文件是否已存在
                file_exists = check_history_file_exists(uploaded_file.name)
                
                if file_exists:
                    # 文件已存在，提示用户选择替换或取消
                    st.warning(f"⚠️ 文件 '{uploaded_file.name}' 已存在于历史记录中")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("✅ 替换为新文件", key="replace_btn", type="primary"):
                            try:
                                # 重置文件指针
                                uploaded_file.seek(0)
                                file_content = uploaded_file.read()
                                data = load_json_from_upload(file_content)
                                is_valid, msg = validate_data_structure(data)
                                
                                if is_valid:
                                    # 覆盖保存
                                    save_path = save_uploaded_file_to_history(uploaded_file.name, file_content)
                                    version_name = uploaded_file.name.replace('.json', '')
                                    st.session_state.data_versions[version_name] = data
                                    st.success(f"✅ 已替换：{uploaded_file.name}")
                                else:
                                    st.error(msg)
                            except Exception as e:
                                st.error(f"解析失败：{str(e)}")
                    
                    with col2:
                        if st.button("❌ 取消上传", key="cancel_upload_btn", type="secondary"):
                            st.info("已取消上传")
                else:
                    # 文件不存在，正常上传
                    try:
                        file_content = uploaded_file.read()
                        data = load_json_from_upload(file_content)
                        is_valid, msg = validate_data_structure(data)
                        
                        if is_valid:
                            # 保存到历史目录
                            save_uploaded_file_to_history(uploaded_file.name, file_content)
                            
                            version_name = uploaded_file.name.replace('.json', '')
                            st.session_state.data_versions[version_name] = data
                            st.success(f"✅ 成功上传：{uploaded_file.name}")
                        else:
                            st.error(msg)
                    except Exception as e:
                        st.error(f"解析失败：{str(e)}")
        
        # 历史文件管理（优化性能：分页+紧凑布局）
        st.markdown("---")
        st.subheader("📂 历史文件管理")
        
        history_files = get_history_json_files()
        if history_files:
            st.markdown(f"**共 {len(history_files)} 个历史文件**")
            
            # 分页机制优化性能
            if "history_page" not in st.session_state:
                st.session_state.history_page = 0
            
            page_size = 10
            total_pages = max(1, (len(history_files) + page_size - 1) // page_size)
            current_page = st.session_state.history_page
            
            start_idx = current_page * page_size
            end_idx = min(start_idx + page_size, len(history_files))
            page_files = history_files[start_idx:end_idx]
            
            # 分页控件
            if total_pages > 1:
                col_p1, col_p2, col_p3 = st.columns([1, 2, 1])
                with col_p1:
                    if st.button("◀ 上一页", disabled=(current_page == 0), key="prev_page"):
                        st.session_state.history_page = max(0, current_page - 1)
                        st.rerun()
                with col_p2:
                    st.markdown(f"<center>第 {current_page + 1} / {total_pages} 页</center>", unsafe_allow_html=True)
                with col_p3:
                    if st.button("下一页 ▶", disabled=(current_page >= total_pages - 1), key="next_page"):
                        st.session_state.history_page = min(total_pages - 1, current_page + 1)
                        st.rerun()
            
            # 显示当前页的文件列表
            # 使用 session_state 跟踪当前展开的文件信息
            if "expanded_file_info" not in st.session_state:
                st.session_state.expanded_file_info = None
            
            for hf in page_files:
                col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
                with col1:
                    st.markdown(f"📁 **{hf['project_name']}**")
                    st.caption(f"⏱️ {hf['upload_time']}")
                with col2:
                    # 加载按钮
                    if st.button("加载", key=f"load_{hf['file_name']}", type="primary"):
                        try:
                            data = load_json_from_file(hf['file_path'])
                            is_valid, msg = validate_data_structure(data)
                            if is_valid:
                                version_name = hf['project_name']
                                if version_name not in st.session_state.data_versions:
                                    st.session_state.data_versions[version_name] = data
                                    st.success(f"✅ 已加载")
                                    st.rerun()
                                else:
                                    st.info("该版本已在当前会话中")
                            else:
                                st.error(f"格式错误：{msg}")
                        except Exception as e:
                            st.error(f"加载失败：{str(e)}")
                with col3:
                    # 文件信息按钮（点击展开/收起）
                    is_expanded = st.session_state.expanded_file_info == hf['file_name']
                    btn_label = "🔽 信息" if is_expanded else "ℹ️ 信息"
                    if st.button(btn_label, key=f"info_{hf['file_name']}", help="点击查看/关闭文件信息"):
                        if is_expanded:
                            st.session_state.expanded_file_info = None
                        else:
                            st.session_state.expanded_file_info = hf['file_name']
                        st.rerun()
                with col4:
                    # 删除按钮
                    if st.button("🗑️", key=f"delete_{hf['file_name']}", type="secondary", help="删除历史文件"):
                        if delete_history_file(hf['file_path']):
                            version_name = hf['project_name']
                            if version_name in st.session_state.data_versions:
                                del st.session_state.data_versions[version_name]
                            # 如果删除的是当前展开的文件，关闭信息面板
                            if st.session_state.expanded_file_info == hf['file_name']:
                                st.session_state.expanded_file_info = None
                            st.success(f"已删除：{hf['project_name']}")
                            st.rerun()
                        else:
                            st.error("删除失败")
                
                # 如果当前文件信息展开，横向显示文件详情
                if st.session_state.expanded_file_info == hf['file_name']:
                    try:
                        file_size = os.path.getsize(hf['file_path']) / 1024
                        info_col1, info_col2, info_col3 = st.columns(3)
                        with info_col1:
                            st.markdown(f"📄 **文件名**：`{hf['file_name']}`")
                        with info_col2:
                            st.markdown(f"📦 **大小**：{file_size:.1f} KB")
                        with info_col3:
                            st.markdown(f"🕐 **修改时间**：{hf['upload_time']}")
                        st.markdown("---")
                    except Exception:
                        pass
        else:
            st.info("暂无历史文件，上传文件后将自动保存")
        
        # 版本管理
        st.markdown("---")
        st.subheader("📋 数据版本管理")
        
        if st.session_state.data_versions:
            version_names = list(st.session_state.data_versions.keys())
            
            # 当前版本选择
            current_version_name = st.selectbox(
                "当前显示版本",
                version_names,
                index=0 if version_names else None
            )
            
            # 对比模式
            compare_mode = st.checkbox("启用对比模式", value=False)
            
            if compare_mode:
                compare_version = st.selectbox(
                    "对比版本",
                    [v for v in version_names if v != current_version_name],
                    index=0 if len(version_names) > 1 else None
                )
                st.session_state.compare_version = compare_version
            else:
                st.session_state.compare_version = None
            
            # 删除版本
            if st.button("删除当前版本", type="secondary"):
                if current_version_name in st.session_state.data_versions:
                    del st.session_state.data_versions[current_version_name]
                    st.success("已删除版本")
                    st.rerun()
            
            if current_version_name:
                loaded_data = st.session_state.data_versions[current_version_name]
        else:
            st.info("暂无数据，请先加载JSON文件")
    
    # 主内容区
    if st.session_state.data_versions:
        # 获取版本列表
        version_names = list(st.session_state.data_versions.keys())
        
        # 默认选择第一个版本
        if "current_version" not in st.session_state:
            st.session_state.current_version = version_names[0]
        
        # 使用 sidebar 中的 current_version_name（如果已选择）
        if 'current_version_name' in locals() and current_version_name:
            st.session_state.current_version = current_version_name
        
        # 获取当前版本数据
        current_version = st.session_state.current_version
        loaded_data = st.session_state.data_versions[current_version]
        
        try:
            structured = loaded_data["structured_output"]
            overview = structured["overview"]
            all_tasks = structured["all_tasks_schedule"]
            critical_tasks = structured.get("critical_path_tasks", [])
            milestones = structured.get("key_milestones", [])
            resource_plan = structured.get("resource_plan", {})
            risks = structured.get("risks", [])
            
            # 获取关键任务ID集合
            critical_task_ids = get_critical_task_ids(critical_tasks)
            
            # 转换为DataFrame
            tasks_df = tasks_to_dataframe(all_tasks, critical_task_ids)
            
            # 获取分部工程映射
            section_mapping = get_section_mapping(all_tasks)
            
            # 项目概览
            render_project_overview(overview)
            
            st.markdown("---")
            
            # 筛选器行
            col_filter1, col_filter2, col_filter3 = st.columns([2, 1, 1])
            
            with col_filter1:
                # 分部工程筛选
                section_options = list(section_mapping.keys())
                section_labels = [f"{k} - {v}" for k, v in section_mapping.items()]
                
                selected_sections = st.multiselect(
                    "按分部工程筛选",
                    options=section_options,
                    format_func=lambda x: f"{x} - {section_mapping.get(x, '')}",
                    default=[],
                    help="选择要显示的分部工程，不选则显示全部"
                )
            
            with col_filter2:
                show_milestones = st.checkbox("显示里程碑", value=True)
            
            with col_filter3:
                show_resource_curve = st.checkbox("显示资源曲线", value=True)
            
            st.markdown("---")

            # 1. 单独显示甘特图（上下各有时间轴）
            st.subheader("📊 施工进度甘特图")
            fig_gantt = create_gantt_chart(
                tasks_df,
                milestones,
                section_filter=selected_sections if selected_sections else None,
                show_milestones=show_milestones,
            )
            st.plotly_chart(
                fig_gantt,
                use_container_width=True,
                key="gantt_chart"
            )
            
            # 2. 单独显示资源负荷曲线（自己的时间轴）
            if show_resource_curve:
                st.markdown("---")
                st.subheader("📈 资源负荷曲线")
                fig_manpower = create_manpower_curve(
                    tasks_df[tasks_df["section_code"].isin(selected_sections)].copy()
                    if selected_sections else tasks_df.copy()
                )
                st.plotly_chart(
                    fig_manpower,
                    use_container_width=True,
                    key="manpower_chart"
                )
            
            # 3. 导出功能：HTML交互式图表（纯Plotly，零额外依赖，最稳妥）
            st.markdown("---")
            col_export1, col_export2 = st.columns([1, 1])

            with col_export1:
                html_key = f"html_{current_version}"
                if html_key not in st.session_state:
                    st.session_state[html_key] = None

                if st.session_state[html_key] is None:
                    if st.button("🌐 生成HTML", key=f"btn_gen_html_{current_version}"):
                        try:
                            progress_bar = st.progress(0, text="正在准备...")
                        except TypeError:
                            progress_bar = st.progress(0)
                        fig_manpower_for_export = create_manpower_curve(
                            tasks_df[tasks_df["section_code"].isin(selected_sections)].copy()
                            if selected_sections else tasks_df.copy()
                        )
                        result = export_combined_html(fig_gantt, fig_manpower_for_export, progress_bar)
                        if result:
                            st.session_state[html_key] = result
                            st.success("HTML生成成功！")
                            st.rerun()

                if st.session_state[html_key] is not None:
                    st.download_button(
                        label="📥 下载(HTML)",
                        data=st.session_state[html_key],
                        file_name=f"{overview.get('project_name', '进度计划')}_进度图.html",
                        mime="text/html",
                        key=f"dl_html_{current_version}"
                    )

            with col_export2:
                csv_data = export_tasks_csv(tasks_df)
                st.download_button(
                    label="📊 导出工序表(CSV)",
                    data=csv_data,
                    file_name=f"{overview.get('project_name', '进度计划')}_工序表.csv",
                    mime="text/csv"
                )
            
            # 工序点击详情 - 使用selectbox选择工序查看详情
            st.markdown("---")
            st.subheader("🔍 工序详情查询")
            
            task_options = [f"{t['task_id']} - {t['task_name']}" for t in all_tasks]
            selected_task_label = st.selectbox(
                "选择工序查看资源配置详情",
                options=task_options,
                index=0
            )
            
            # 找到选中的任务
            selected_task_id = selected_task_label.split(" - ")[0]
            selected_task = next(
                (t for t in all_tasks if t["task_id"] == selected_task_id),
                None
            )
            
            if selected_task:
                render_resource_detail(selected_task)
            
            # 里程碑表格
            st.markdown("---")
            render_milestones_table(milestones)
            
            # 资源计划
            st.markdown("---")
            render_resource_plan(resource_plan)
            
            # 风险列表
            if risks:
                st.markdown("---")
                render_risks(risks)
            
            # 对比模式 - 工期偏差分析
            if st.session_state.get("compare_version"):
                compare_version_name = st.session_state.compare_version
                compare_data = st.session_state.data_versions.get(compare_version_name)
                
                if compare_data:
                    st.markdown("---")
                    st.subheader("📊 版本对比分析")
                    
                    compare_structured = compare_data["structured_output"]
                    compare_overview = compare_structured["overview"]
                    compare_tasks = compare_structured["all_tasks_schedule"]
                    
                    col_c1, col_c2, col_c3 = st.columns(3)
                    
                    with col_c1:
                        delta_days = (
                            overview.get("total_duration_days", 0) 
                            - compare_overview.get("total_duration_days", 0)
                        )
                        st.metric(
                            label="工期偏差",
                            value=f"{delta_days:+d} 天",
                            delta=f"相对{compare_version_name}"
                        )
                    
                    with col_c2:
                        st.metric(
                            label=f"{current_version_name} 工期",
                            value=f"{overview.get('total_duration_days', 0)} 天"
                        )
                    
                    with col_c3:
                        st.metric(
                            label=f"{compare_version_name} 工期",
                            value=f"{compare_overview.get('total_duration_days', 0)} 天"
                        )
                    
                    # 工序数量对比
                    col_c4, col_c5, col_c6 = st.columns(3)
                    
                    with col_c4:
                        task_diff = len(all_tasks) - len(compare_tasks)
                        st.metric(
                            label="工序数量变化",
                            value=f"{task_diff:+d} 项",
                            delta=f"共{len(all_tasks)}项"
                        )
                    
                    with col_c5:
                        st.metric(
                            label=f"{current_version_name} 工序数",
                            value=f"{len(all_tasks)} 项"
                        )
                    
                    with col_c6:
                        st.metric(
                            label=f"{compare_version_name} 工序数",
                            value=f"{len(compare_tasks)} 项"
                        )
        
        except Exception as e:
            st.error(f"渲染数据时发生错误：{str(e)}")
            st.exception(e)
    
    else:
        # 无数据时的欢迎界面
        st.info("👈 请从左侧边栏加载JSON数据开始使用")
        
        st.markdown("### 📖 使用说明")
        st.markdown("""
        1. **加载数据**：从左侧边栏选择本地JSON文件或上传文件
        2. **查看概览**：页面顶部显示项目基本信息
        3. **甘特图浏览**：查看完整进度计划，红色为关键线路，蓝色为非关键工序
        4. **筛选工序**：使用分部工程筛选器查看特定阶段的工序
        5. **资源详情**：在下方选择工序查看详细资源配置
        6. **版本对比**：加载多组数据后启用对比模式，查看工期偏差
        7. **数据导出**：将甘特图导出为HTML交互式图表，工序表导出为CSV
        """)
        
        st.markdown("### 📋 JSON数据格式要求")
        st.code("""
{
  "structured_output": {
    "overview": {
      "project_name": "项目名称",
      "total_duration_days": 210,
      "planned_start_date": "2026-03-01",
      "planned_end_date": "2026-09-27",
      "critical_path_length": 25
    },
    "key_milestones": [
      { "name": "里程碑名称", "date": "2026-04-15", "task_id": "2.1.2", "description": "说明" }
    ],
    "critical_path_tasks": [
      { "task_id": "1.1.1", "task_name": "工序名", "start_date": "2026-03-01", 
        "finish_date": "2026-03-10", "duration_days": 10, "assigned_resources": {...} }
    ],
    "all_tasks_schedule": [
      { "task_id": "1.1.1", "task_name": "工序名", "start_date": "2026-03-01", 
        "finish_date": "2026-03-10", "duration_days": 10, "assigned_resources": {...} }
    ],
    "resource_plan": {
      "total_manpower_days": 18500,
      "peak_manpower": 145,
      "equipment_peak": {...},
      "material_summary": [...]
    },
    "risks": [
      { "risk_name": "风险名称", "mitigation": "应对措施" }
    ]
  }
}
        """, language="json")


if __name__ == "__main__":
    main()

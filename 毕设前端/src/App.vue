<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts/core'
import { BarChart, CustomChart, LineChart, RadarChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  RadarComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

echarts.use([
  BarChart,
  CustomChart,
  LineChart,
  RadarChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  RadarComponent,
  TooltipComponent,
  CanvasRenderer,
])

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5001').replace(/\/$/, '')
const KPI_TREE_API = `${API_BASE_URL}/api/workshop/kpi`
const EVENTS_API = `${API_BASE_URL}/api/events`
const AGENT_API = `${API_BASE_URL}/api/agent/chat`
const newConversationId = () => globalThis.crypto?.randomUUID?.() || `local-${Date.now()}-${Math.random().toString(16).slice(2)}`
const agentConversationId = ref(newConversationId())
const DEFAULT_GANTT_WINDOW_MINUTES = 2
const GANTT_WINDOW_OPTIONS = [2, 5, 15, 30]
const DIAG_STATE_LABELS = {
  srun: '加工运行',
  ssby: '待机空转',
  su_down: '故障停机',
  sp_down: '计划停机',
  soff: '离线',
  swork: '作业运行',
  swait: '空闲等待',
  steach: '示教调试',
  sgrip: '抓取放置',
  salarm: '异常报警',
  sstop: '停止模式',
  serror: '系统故障',
  scomm_err: '通信中断',
}
const GANTT_STATE_COLOR_MAP = {
  srun: '#67c23a',
  swork: '#67c23a',
  sgrip: '#95d475',
  ssby: '#e6a23c',
  swait: '#e6a23c',
  steach: '#909399',
  sp_down: '#909399',
  su_down: '#f56c6c',
  salarm: '#f56c6c',
  serror: '#f56c6c',
  soff: '#606266',
  scomm_err: '#303133',
  sstop: '#909399',
}
const GANTT_STATE_LEGEND = [
  { key: 'srun', label: '加工/作业运行' },
  { key: 'ssby', label: '待机/等待' },
  { key: 'steach', label: '计划停机/示教' },
  { key: 'su_down', label: '故障停机/报警' },
  { key: 'soff', label: '离线/中断' },
]
const GANTT_EQUIP_DISPLAY_MAP = {
  Siemens_PLC_S7_03: 'PLC_03',
  Siemens_PLC_S7_02: 'PLC_02',
  'S7-PLC-001': 'PLC_01',
  KUKA_Robot_R2000_03: 'Robot_03',
  KUKA_Robot_R2000_02: 'Robot_02',
  'KUKA-R2000-001': 'Robot_01',
  Baoji_CNC_01_03: 'CNC_03',
  Baoji_CNC_01_02: 'CNC_02',
  'BJ-CNC-001': 'CNC_01',
}
const GANTT_DISPLAY_ORDER = [
  'PLC_03',
  'PLC_02',
  'PLC_01',
  'Robot_03',
  'Robot_02',
  'Robot_01',
  'CNC_03',
  'CNC_02',
  'CNC_01',
]

const loading = ref(false)
const error = ref('')
const workshop = ref(null)
const selectedNodeKey = ref('workshop')
const selectedPayload = ref(null)
const lastRefreshAt = ref('')
const targetDate = ref('')
let pollTimer = null
let radarChart = null
let paretoChart = null
let ganttChart = null
const radarRef = ref(null)
const paretoRef = ref(null)
const ganttRef = ref(null)
const diagLoading = ref(false)
const diagError = ref('')
const ganttWindowMinutes = ref(DEFAULT_GANTT_WINDOW_MINUTES)
const diagPayload = ref({
  events: [],
  alarm_top5: [],
  loss_breakdown: [],
})
const agentQuestion = ref('')
const agentLoading = ref(false)
const agentError = ref('')
const agentConversationRef = ref(null)
const agentMessages = ref([
  {
    role: 'assistant',
    content: '我可以查询设备与产线 KPI、状态/故障时间线和遥测摘要，也可以检索独立的工业诊断知识库。当前设备数据来自仿真环境，回答会标注证据与限制。',
    trace: [],
  },
])

const localDateText = () => {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const suggestedAgentQuestion = computed(() => {
  const payload = selectedPayload.value
  if (payload?.node_type === 'station' && payload.equip_id) {
    return `查询 ${payload.equip_id} 在 ${localDateText()} 的 KPI 和故障事件，判断数据是否可靠，并引用事件 ID。`
  }
  return 'CNC 的 OEE 下降时应该按什么顺序排查？请引用知识库。'
})

const formatLocalTime = (value = new Date()) => value.toLocaleTimeString('zh-CN', { hour12: false })

function renderAgentMarkdown(content) {
  const html = marked.parse(String(content || ''), { breaks: true, gfm: true })
  return DOMPurify.sanitize(html)
}

async function scrollAgentToBottom() {
  await nextTick()
  const element = agentConversationRef.value
  if (element) element.scrollTop = element.scrollHeight
}

const treeData = computed(() => {
  const ws = workshop.value
  if (!ws) return []
  return [
    {
      key: 'workshop',
      label: '仿真车间',
      nodeType: 'workshop',
      payload: ws,
      children: (ws.lines || []).map((line) => ({
        key: `line-${line.line_index}`,
        label: `产线${line.line_index}`,
        nodeType: 'line',
        payload: line,
        children: (line.stations || []).map((st) => ({
          key: `line-${line.line_index}-station-${st.station_no}`,
          label: st.label,
          nodeType: 'station',
          payload: st,
        })),
      })),
    },
  ]
})

const currentNodeType = computed(() => selectedPayload.value?.node_type || 'workshop')
const showStationRadar = computed(
  () => currentNodeType.value !== 'station' || selectedPayload.value?.device_type === 'cnc'
)

const stationPercentCards = computed(() => {
  const payload = selectedPayload.value || {}
  const metrics = payload.metrics || {}
  const isCnc = payload.device_type === 'cnc'
  const isRobot = payload.device_type === 'robot'
  const isPlc = payload.device_type === 'plc'
  if (isCnc) {
    return [
      { label: 'OEE', value: Number(metrics.oee || 0) },
      { label: '时间开动率', value: Number(metrics.availability || 0) },
      { label: '性能开动率', value: Number(metrics.performance || 0) },
      { label: '质量指数', value: Number(metrics.quality || 0) },
    ]
  }
  if (isRobot) {
    return [
      { label: '时间开动率', value: Number(metrics.availability || 0) },
    ]
  }
  if (isPlc) {
    return [
      { label: '时间开动率', value: Number(metrics.availability || 0) },
    ]
  }
  return []
})

const linePercentCards = computed(() => {
  const metrics = selectedPayload.value?.metrics || {}
  return [
    { label: 'OEE', value: Number(metrics.oee || 0) },
    { label: '良品率', value: Number(metrics.good_rate || 0) },
    { label: '设备维护率', value: Number(metrics.maintenance_rate || 0) },
    { label: '无故障时间率', value: Number(metrics.fault_free_rate || 0) },
  ]
})

const workshopPercentCards = computed(() => {
  const metrics = selectedPayload.value?.metrics || {}
  return [
    { label: 'OEE', value: Number(metrics.oee || 0) },
    { label: '良品率', value: Number(metrics.good_rate || 0) },
    { label: '设备维护率', value: Number(metrics.maintenance_rate || 0) },
    { label: '设备完好率', value: Number(metrics.equipment_health_rate || 0) },
  ]
})

const percentMetricCards = computed(() => {
  if (currentNodeType.value === 'station') {
    return stationPercentCards.value
  }
  if (currentNodeType.value === 'line') {
    return linePercentCards.value
  }
  return workshopPercentCards.value
})

const selectedDataWarnings = computed(() => {
  const labels = {
    telemetry_gaps_detected: '采集存在时间空档，指标仅代表已观测片段',
    counter_reset_detected: '检测到产量计数重置，重置跳变未计入产量',
    insufficient_counter_samples: '产量采样不足，性能与质量指标不可直接解释',
    open_event_capped_at_as_of: '存在尚未结束的状态事件，时长暂截至本次查询时刻',
    overlapping_state_events: '检测到状态事件重叠，当前指标不宜用于结论',
  }
  return (selectedPayload.value?.warnings || []).map((item) => labels[item] || item)
})

const stationNumericCards = computed(() => {
  if (currentNodeType.value !== 'station') return []
  const payload = selectedPayload.value || {}
  const metrics = payload.metrics || {}
  const isRobot = payload.device_type === 'robot'
  const isPlc = payload.device_type === 'plc'
  if (isRobot) {
    return [
      { label: 'MTBF', value: Number(metrics.mtbf || 0) },
      { label: 'MTTR', value: Number(metrics.mttr || 0) },
    ]
  }
  if (isPlc) {
    return [
      { label: 'MTBF', value: Number(metrics.mtbf || metrics.mtvf || 0) },
      { label: 'MTTR', value: Number(metrics.mttr || 0) },
    ]
  }
  return [
    { label: 'MTBF', value: Number(metrics.mtbf || 0) },
    { label: 'MTTR', value: Number(metrics.mttr || 0) },
  ]
})

const selectedEquipIds = computed(() => {
  const payload = selectedPayload.value
  if (!payload) return []
  if (payload.node_type === 'station') {
    return payload.equip_id ? [payload.equip_id] : []
  }
  if (payload.node_type === 'line') {
    return (payload.stations || []).map((x) => x.equip_id).filter(Boolean)
  }
  if (payload.node_type === 'workshop') {
    return (payload.lines || [])
      .flatMap((line) => (line.stations || []).map((x) => x.equip_id))
      .filter(Boolean)
  }
  return []
})

const kpiAlerts = computed(() => {
  const payload = selectedPayload.value || {}
  const metrics = payload.metrics || {}
  const nodeType = payload.node_type
  const alerts = []
  if (!nodeType) return alerts

  const oee = Number(metrics.oee || 0)
  const availability = Number(metrics.availability || metrics.availability_weighted || 0)
  const maintenance = Number(metrics.maintenance_rate || 0)
  const healthRate = Number(metrics.equipment_health_rate || 0)

  if (nodeType === 'station') {
    if (payload.device_type === 'cnc') {
      if (oee < 70) alerts.push(`当前工位 OEE 为 ${oee.toFixed(2)}%，低于演示阈值 70%。`)
      if (availability < 85) alerts.push(`当前工位时间开动率为 ${availability.toFixed(2)}%，低于演示阈值 85%。`)
    } else {
      if (availability < 85) alerts.push(`当前工位时间开动率为 ${availability.toFixed(2)}%，低于演示阈值 85%。`)
    }
    if (maintenance > 20) alerts.push(`当前工位维护率为 ${maintenance.toFixed(2)}%，高于演示阈值 20%。`)
    return alerts
  }

  if (nodeType === 'line') {
    if (oee < 75) alerts.push(`当前产线 OEE 为 ${oee.toFixed(2)}%，低于演示阈值 75%。`)
    if (maintenance > 18) alerts.push(`当前产线设备维护率为 ${maintenance.toFixed(2)}%，高于演示阈值 18%。`)
    return alerts
  }

  if (nodeType === 'workshop') {
    if (oee < 78) alerts.push(`当前车间 OEE 为 ${oee.toFixed(2)}%，低于演示阈值 78%。`)
    if (maintenance > 18) alerts.push(`当前车间设备维护率为 ${maintenance.toFixed(2)}%，高于演示阈值 18%。`)
    if (healthRate > 0 && healthRate < 90) alerts.push(`当前车间设备完好率为 ${healthRate.toFixed(2)}%，低于演示阈值 90%。`)
    return alerts
  }

  return alerts
})

const monitorInsights = computed(() => {
  const payload = selectedPayload.value || {}
  const metrics = payload.metrics || {}
  const nodeType = payload.node_type || currentNodeType.value
  const oee = Number(metrics.oee || 0)
  const availability = Number(metrics.availability || metrics.availability_weighted || 0)
  const maintenance = Number(metrics.maintenance_rate || 0)
  const goodRate = Number(metrics.good_rate || 0)
  const healthRate = Number(metrics.equipment_health_rate || 0)
  const label = payload.label || '当前对象'

  if (!nodeType) return ['监控指标正在加载中，稍后会显示当前对象的运行特征。']
  if (nodeType === 'station') {
    return [
      `${label} 当前时间开动率为 ${availability.toFixed(1)}%，${availability >= 85 ? '设备处于较稳定运行区间。' : '低于建议阈值，存在停机或等待偏高问题。'}`,
      payload.device_type === 'cnc'
        ? `该工位 OEE 为 ${oee.toFixed(1)}%，可用于衡量“可用性+性能+质量”的综合产出水平。`
        : `该工位维护率为 ${maintenance.toFixed(1)}%，可用于判断是否存在频繁检修或异常干预。`,
      `当前预警项数量为 ${kpiAlerts.value.length}，${kpiAlerts.value.length ? '建议优先处理预警中重复出现的指标。' : '当前无阈值告警。'}`,
    ]
  }
  if (nodeType === 'line') {
    return [
      `${label} 当前 OEE 为 ${oee.toFixed(1)}%，良品率 ${goodRate.toFixed(1)}%，反映了产线效率与质量的综合表现。`,
      `设备维护率为 ${maintenance.toFixed(1)}%，${maintenance > 18 ? '维护负担偏高，可能拉低有效产出时间。' : '维护负担处于可控区间。'}`,
      `本产线纳入监控设备共 ${selectedEquipIds.value.length} 台，适合结合甘特图定位“在哪台设备上损失最集中”。`,
    ]
  }
  return [
    `车间当前 OEE 为 ${oee.toFixed(1)}%，良品率 ${goodRate.toFixed(1)}%，用于衡量全局产出效率与质量水平。`,
    `设备维护率 ${maintenance.toFixed(1)}%，设备完好率 ${healthRate.toFixed(1)}%，可反映车间健康度与运维压力。`,
    `当前监控范围共 ${selectedEquipIds.value.length} 台设备，建议按“产线-工位-设备”逐层下钻分析异常来源。`,
  ]
})

const monitorOptimizations = computed(() => {
  const payload = selectedPayload.value || {}
  const metrics = payload.metrics || {}
  const nodeType = payload.node_type || currentNodeType.value
  const oee = Number(metrics.oee || 0)
  const availability = Number(metrics.availability || metrics.availability_weighted || 0)
  const maintenance = Number(metrics.maintenance_rate || 0)
  const actions = []
  if (nodeType === 'station') {
    if (availability < 85) actions.push('优先减少非计划停机，先检查最近高频报警与通信中断。')
    if (payload.device_type === 'cnc' && oee < 70) actions.push('针对低 OEE 工位，先压降等待时间，再优化程序节拍与换型流程。')
    if (maintenance > 20) actions.push('维护率偏高，建议按故障类型做预防性点检，降低重复维修。')
  } else if (nodeType === 'line') {
    if (oee < 75) actions.push('先抓影响最大的1~2个工位做短周期改善，避免全线同时改动导致验证困难。')
    if (maintenance > 18) actions.push('建立班次级维修复盘，优先处理停机时长长且复发高的问题。')
  } else {
    if (oee < 78) actions.push('按产线做 OEE 排名，优先扶正低于均值的产线。')
    if (maintenance > 18) actions.push('建立车间级报警周榜，压降高频故障码触发次数。')
  }
  if (!actions.length) {
    actions.push('当前监控指标整体稳定，建议维持现有参数并持续观察趋势变化。')
  }
  actions.push('每次优化后至少连续观察1~2天，确认指标改善具有持续性。')
  return actions
})

const ganttWindowEvents = computed(() => {
  const nowMs = Date.now()
  const windowStartMs = nowMs - Number(ganttWindowMinutes.value || DEFAULT_GANTT_WINDOW_MINUTES) * 60 * 1000
  return (diagPayload.value.events || [])
    .filter((x) => x.start_time && x.end_time)
    .map((x) => {
      const rawStart = new Date(x.start_time).getTime()
      const rawEnd = new Date(x.end_time).getTime()
      const startMs = Math.max(rawStart, windowStartMs)
      const endMs = Math.min(rawEnd, nowMs)
      return {
        equip_id: x.equip_id,
        state_code: x.state_code || 'unknown',
        alarm_code: Number(x.alarm_code || 0),
        alarm_meta: x.alarm_meta || null,
        start_ms: startMs,
        end_ms: endMs,
        duration_sec: Math.max(0, Math.round((endMs - startMs) / 1000)),
      }
    })
    .filter((x) => x.duration_sec > 0)
})

const paretoEventRows = computed(() => {
  const nowMs = Date.now()
  return (diagPayload.value.events || [])
    .filter((x) => x.start_time && x.end_time)
    .map((x) => {
      const startMs = new Date(x.start_time).getTime()
      const endMs = new Date(x.end_time).getTime()
      const fallbackSec = Math.max(0, Math.round((Math.min(endMs, nowMs) - startMs) / 1000))
      return {
        equip_id: x.equip_id,
        alarm_code: Number(x.alarm_code || 0),
        alarm_meta: x.alarm_meta || null,
        duration_sec: Math.max(0, Number(x.duration_sec || fallbackSec)),
      }
    })
    .filter((x) => x.duration_sec > 0)
})

const paretoAlarmStats = computed(() => {
  const byCode = new Map()
  for (const row of paretoEventRows.value) {
    const alarmCode = Number(row.alarm_code || 0)
    if (alarmCode <= 0) continue
    const key = String(alarmCode)
    if (!byCode.has(key)) {
      byCode.set(key, {
        alarm_code: alarmCode,
        alarm_meta: row.alarm_meta || null,
        count: 0,
        duration_sec: 0,
        equip_ids: new Set(),
      })
    }
    const stat = byCode.get(key)
    stat.count += 1
    stat.duration_sec += Number(row.duration_sec || 0)
    stat.equip_ids.add(row.equip_id)
    if (!stat.alarm_meta && row.alarm_meta) stat.alarm_meta = row.alarm_meta
  }
  return [...byCode.values()]
    .map((x) => ({ ...x, equip_ids: [...x.equip_ids] }))
    .sort((a, b) => Number(b.duration_sec || 0) - Number(a.duration_sec || 0))
})

const paretoInsights = computed(() => {
  const top = paretoAlarmStats.value
  const observedTotalSec = paretoEventRows.value.reduce((s, x) => s + Number(x.duration_sec || 0), 0)
  if (!top.length) {
    return [
      '当前统计周期内未观察到故障停机；这不能单独证明设备正常，还需结合采集覆盖与在线状态。',
      '建议继续保持观察；一旦出现报警，优先处理“停机时长长 + 重复出现”的故障类型，改善效果通常最明显。',
    ]
  }
  const totalDur = top.reduce((s, x) => s + Number(x.duration_sec || 0), 0)
  const first = top[0]
  const firstName = first?.alarm_meta?.alarm_label || `报警${first.alarm_code}`
  const firstPct = totalDur > 0 ? (Number(first.duration_sec || 0) / totalDur) * 100 : 0
  const top3Dur = top.slice(0, 3).reduce((s, x) => s + Number(x.duration_sec || 0), 0)
  const top3Pct = totalDur > 0 ? (top3Dur / totalDur) * 100 : 0
  const firstDur = Number(first?.duration_sec || 0)
  const affectedEquipNames = (first?.equip_ids || [])
    .slice(0, 2)
    .map((id) => GANTT_EQUIP_DISPLAY_MAP[id] || id)
    .filter(Boolean)
  const affectedEquipText = affectedEquipNames.length ? affectedEquipNames.join('、') : '当前筛选设备'
  const faultLoadPct = observedTotalSec > 0 ? (totalDur / observedTotalSec) * 100 : 0
  const action = (first?.alarm_meta?.suggested_action || '结合现场日志进行根因排查').replace(/[。！？!?]+$/g, '')
  const concentrationText =
    firstPct >= 50
      ? '故障高度集中，适合单点突破'
      : firstPct >= 30
      ? '故障有明显主因，建议先抓前两类'
      : '故障分散，建议按设备分组并行排查'
  const pressureText =
    faultLoadPct >= 20
      ? '故障停机压力较高，建议立即安排纠偏'
      : faultLoadPct >= 10
      ? '故障停机压力中等，需要持续跟踪'
      : '故障停机压力可控，建议维持预防性措施'
  return [
    `当前统计周期内，首要故障为【${firstName}】，累计停机约 ${formatDurationHuman(firstDur)}，占故障停机总量 ${firstPct.toFixed(1)}%。`,
    `前3类故障累计占比 ${top3Pct.toFixed(1)}%，${concentrationText}；故障停机在当前已观测事件中的占比约 ${faultLoadPct.toFixed(1)}%，${pressureText}。`,
    `该主故障当前主要影响 ${affectedEquipText}，建议先从这些设备及其上下游信号联锁关系入手排查。`,
    `待验证排查方向：${action}。验证方式可用“优化前后 1~2 天同口径对比”，观察主故障占比是否持续下降。`,
  ]
})

const paretoOptimizations = computed(() => {
  const top = paretoAlarmStats.value
  if (!top.length) {
    return [
      '当前窗口无故障停机，建议继续维持点检频率并保留报警趋势观察。',
      '可将时间窗口切到15/30分钟，确认更长区间内是否仍保持稳定。',
    ]
  }
  const first = top[0]
  const firstName = first?.alarm_meta?.alarm_label || `报警${first.alarm_code}`
  const firstEquip = (first?.equip_ids || [])
    .slice(0, 2)
    .map((id) => GANTT_EQUIP_DISPLAY_MAP[id] || id)
    .join('、')
  const action = (first?.alarm_meta?.suggested_action || '结合现场报警日志做根因排查').replace(/[。！？!?]+$/g, '')
  return [
    `优先处理【${firstName}】并聚焦 ${firstEquip || '相关设备'}，先做单点验证再扩展到同类设备。`,
    `建议核查项：${action}。`,
    '用“优化前后同窗口对比”验证效果，目标是主故障时长和占比同时下降。',
  ]
})

const ganttInsights = computed(() => {
  const rows = ganttWindowEvents.value
  const allEquipCount = Math.max(1, selectedEquipIds.value.length)
  const cncEquipCount = Math.max(1, selectedEquipIds.value.filter((id) => isCncEquipId(id)).length)
  const minutes = Number(ganttWindowMinutes.value || DEFAULT_GANTT_WINDOW_MINUTES)
  const theoreticalCncSec = cncEquipCount * minutes * 60
  const theoreticalAllSec = allEquipCount * minutes * 60
  if (!rows.length) {
    return [
      `最近${minutes}分钟没有采到有效状态事件。`,
      '这通常是当前筛选范围内设备较少、或该时间段设备状态变化不明显导致。可切换到车间级或延长观察时间。',
    ]
  }
  const observedTotal = rows.reduce((s, x) => s + x.duration_sec, 0)
  // 分析口径：仅 CNC 的加工运行(srun)计为增值。
  // Robot/PLC 运行在本分析里视为协同保障，不计入直接增值。
  const valSet = new Set(['srun'])
  const downSet = new Set(['su_down', 'salarm', 'serror', 'scomm_err', 'soff'])
  const waitSet = new Set(['ssby', 'swait', 'steach', 'sp_down', 'sstop'])
  let valSec = 0
  let downSec = 0
  let waitSec = 0
  const byEquip = new Map()
  const intervalsByEquip = new Map()
  for (const row of rows) {
    const equipId = row.equip_id
    if (!byEquip.has(equipId)) {
      byEquip.set(equipId, { total: 0, val: 0, down: 0, wait: 0 })
    }
    if (!intervalsByEquip.has(equipId)) intervalsByEquip.set(equipId, [])
    intervalsByEquip.get(equipId).push([row.start_ms, row.end_ms])
    const stat = byEquip.get(equipId)
    stat.total += row.duration_sec
    if (valSet.has(row.state_code) && isCncEquipId(row.equip_id)) valSec += row.duration_sec
    if (valSet.has(row.state_code) && isCncEquipId(row.equip_id)) stat.val += row.duration_sec
    else if (downSet.has(row.state_code)) {
      downSec += row.duration_sec
      stat.down += row.duration_sec
    } else if (waitSet.has(row.state_code)) {
      waitSec += row.duration_sec
      stat.wait += row.duration_sec
    }
  }
  const equipRows = [...byEquip.entries()].map(([equipId, stat]) => {
    const loss = stat.down + stat.wait
    const lossPct = stat.total > 0 ? (loss / stat.total) * 100 : 0
    const downPctEquip = stat.total > 0 ? (stat.down / stat.total) * 100 : 0
    const waitPctEquip = stat.total > 0 ? (stat.wait / stat.total) * 100 : 0
    return { equipId, ...stat, loss, lossPct, downPctEquip, waitPctEquip }
  })
  const topLossEquip = [...equipRows].sort((a, b) => b.lossPct - a.lossPct)[0]
  const valPct = theoreticalCncSec > 0 ? (valSec / theoreticalCncSec) * 100 : 0
  const downPct = theoreticalAllSec > 0 ? (downSec / theoreticalAllSec) * 100 : 0
  const waitPct = theoreticalAllSec > 0 ? (waitSec / theoreticalAllSec) * 100 : 0
  const topLossEquipName = topLossEquip
    ? GANTT_EQUIP_DISPLAY_MAP[topLossEquip.equipId] || topLossEquip.equipId
    : '未知设备'
  const valDur = formatDurationHuman(valSec)
  const totalDur = formatDurationHuman(theoreticalCncSec)
  const totalAllDur = formatDurationHuman(theoreticalAllSec)
  const observedDur = formatDurationHuman(observedTotal)
  const rawCoveragePct = theoreticalAllSec > 0 ? (observedTotal / theoreticalAllSec) * 100 : 0
  const coveragePct = Math.min(100, rawCoveragePct)
  let overlapSec = 0
  for (const intervals of intervalsByEquip.values()) {
    intervals.sort((a, b) => a[0] - b[0])
    let lastEnd = -1
    for (const [start, end] of intervals) {
      if (lastEnd >= 0 && start < lastEnd) overlapSec += Math.max(0, Math.min(end, lastEnd) - start)
      if (end > lastEnd) lastEnd = end
    }
  }
  const overlapWarning =
    overlapSec > 0
      ? `检测到约 ${formatDurationHuman(overlapSec / 1000)} 的同设备状态重叠，通常由上报时钟偏差或状态切换延迟造成，建议结合原始日志核对。`
      : ''
  const dominantLoss = downPct >= waitPct ? '停机/离线' : '等待/计划'
  const keyAction =
    downPct >= waitPct
      ? '优先压降故障与离线触发频次，先处理高频报警和通信中断。'
      : '优先优化上下游节拍与切换逻辑，减少“机器在等料/等信号”的空转等待。'
  return [
    `最近${minutes}分钟、按 ${cncEquipCount} 台 CNC 计，理论总时长上限为 ${totalDur}（例如3台CNC就是6分钟）。按当前分析口径（仅将 CNC 的加工运行 srun 计为增值），增值状态累计约 ${valDur}，折算占比 ${valPct.toFixed(1)}%。Robot/PLC 的运行在这里作为协同保障时间，不计入直接增值。`,
    `若按全部设备（共 ${allEquipCount} 台）计算，同期理论总时长为 ${totalAllDur}；其中停机/离线占比 ${downPct.toFixed(1)}%，等待/计划类占比 ${waitPct.toFixed(1)}%，当前主要损失来源为${dominantLoss}。`,
    `损失占比最高的设备是 ${topLossEquipName}，其“停机+等待”约占本设备可观测时长 ${Number(topLossEquip?.lossPct || 0).toFixed(1)}%（停机 ${Number(topLossEquip?.downPctEquip || 0).toFixed(1)}%，等待 ${Number(topLossEquip?.waitPctEquip || 0).toFixed(1)}%）。`,
    `本次事件片段累计约 ${observedDur}，覆盖度约 ${coveragePct.toFixed(1)}%。覆盖度偏低时结论仅作趋势参考，建议切换到15/30分钟窗口复核。${overlapWarning}`,
    `可执行建议：${keyAction} 先做一轮小步优化，再对比“增值占比、停机占比、等待占比”三项是否同步改善。`,
  ]
})

const ganttOptimizations = computed(() => {
  const rows = ganttWindowEvents.value
  if (!rows.length) {
    return [
      '当前窗口无有效状态事件，建议切换到15/30分钟再判断是否存在节拍或停机问题。',
      '若仍无数据，优先检查采集链路和设备在线状态。',
    ]
  }
  const downSet = new Set(['su_down', 'salarm', 'serror', 'scomm_err', 'soff'])
  const waitSet = new Set(['ssby', 'swait', 'steach', 'sp_down', 'sstop'])
  let downSec = 0
  let waitSec = 0
  for (const row of rows) {
    if (downSet.has(row.state_code)) downSec += row.duration_sec
    if (waitSet.has(row.state_code)) waitSec += row.duration_sec
  }
  const dominantLoss = downSec >= waitSec ? '停机/离线' : '等待/计划'
  return downSec >= waitSec
    ? [
        `当前主要损失是${dominantLoss}，建议优先压降故障和离线触发频次。`,
        '先处理高频报警设备，再排查通信抖动与异常重连。',
        '目标是先把停机占比压下去，再做节拍优化。',
      ]
    : [
        `当前主要损失是${dominantLoss}，建议优先优化上下游节拍与切换逻辑。`,
        '重点检查“前工位停机导致后工位等待”的连锁现象。',
        '目标是降低等待占比并同步提升增值占比。',
      ]
})

function formatPercent(v) {
  return `${Number(v || 0).toFixed(2)}%`
}

function formatPercentOrDash(v) {
  if (v === null || v === undefined) return '-'
  return `${Number(v).toFixed(2)}%`
}

function formatMinuteOrDash(v) {
  if (v === null || v === undefined) return '-'
  return `${Number(v).toFixed(2)} min`
}

function formatDurationHuman(sec) {
  const s = Math.max(0, Math.round(Number(sec || 0)))
  const m = Math.floor(s / 60)
  const r = s % 60
  if (m <= 0) return `${r}秒`
  if (r === 0) return `${m}分`
  return `${m}分${r}秒`
}

function isCncEquipId(equipId) {
  const token = String(equipId || '').toLowerCase()
  return token.includes('cnc') || token.includes('baoji') || token.includes('bj-cnc')
}

function wrapAxisLabel(text, size = 6) {
  const raw = String(text || '')
  if (!raw) return ''
  const chunks = []
  for (let i = 0; i < raw.length; i += size) {
    chunks.push(raw.slice(i, i + size))
  }
  return chunks.join('\n')
}

function lineStationMetricText(station, key) {
  const m = station?.metrics || {}
  if (key === 'oee') {
    return station?.device_type === 'cnc' ? formatPercentOrDash(m.oee) : '-'
  }
  if (key === 'performance') {
    return station?.device_type === 'cnc' ? formatPercentOrDash(m.performance) : '-'
  }
  if (key === 'quality') {
    return station?.device_type === 'cnc' ? formatPercentOrDash(m.quality) : '-'
  }
  if (key === 'availability') return formatPercentOrDash(m.availability)
  if (key === 'mtbf') return formatMinuteOrDash(m.mtbf)
  if (key === 'mttr') return formatMinuteOrDash(m.mttr)
  return '-'
}

function handleNodeClick(data) {
  selectedNodeKey.value = data.key
  selectedPayload.value = data.payload
}

function ensureCharts() {
  if (!showStationRadar.value && currentNodeType.value === 'station' && radarChart) {
    radarChart.dispose()
    radarChart = null
  }
  if (radarChart && radarRef.value && radarChart.getDom() !== radarRef.value) {
    radarChart.dispose()
    radarChart = null
  }
  if (!radarChart && radarRef.value) {
    radarChart = echarts.init(radarRef.value)
  }
}

function buildRadarOption() {
  const metrics = percentMetricCards.value
  const labels = metrics.map((x) => x.label)
  const values = metrics.map((x) => Number(x.value || 0))
  return {
    title: {
      text: '指标雷达图',
      left: 'center',
      top: 2,
      textStyle: { color: '#303133', fontSize: 14 },
    },
    tooltip: {},
    radar: {
      radius: '62%',
      indicator: labels.map((name) => ({ name, max: 100 })),
      axisName: { color: '#606266' },
      splitLine: { lineStyle: { color: '#dcdfe6' } },
      splitArea: { areaStyle: { color: ['#f8fafc', '#f3f6fb'] } },
      axisLine: { lineStyle: { color: '#c0c4cc' } },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: values,
            name: '当前指标',
            areaStyle: { color: 'rgba(64, 158, 255, 0.25)' },
            lineStyle: { color: '#409eff', width: 2 },
          },
        ],
      },
    ],
  }
}

function renderCharts() {
  if (!showStationRadar.value && currentNodeType.value === 'station') return
  ensureCharts()
  if (radarChart) {
    radarChart.setOption(buildRadarOption(), true)
  }
}

function ensureDiagnosticCharts() {
  if (!paretoChart && paretoRef.value) {
    paretoChart = echarts.init(paretoRef.value)
  }
  if (!ganttChart && ganttRef.value) {
    ganttChart = echarts.init(ganttRef.value)
  }
}

function buildParetoOption() {
  const source = paretoAlarmStats.value.slice(0, 9)
  const labels = source.map((x) => x?.alarm_meta?.alarm_label || `报警${x.alarm_code}`)
  const bars = source.map((x) => Number(x.duration_sec || 0))
  const total = bars.reduce((s, v) => s + v, 0)
  let acc = 0
  const cumulative = bars.map((v) => {
    acc += v
    return total > 0 ? Number(((acc / total) * 100).toFixed(2)) : 0
  })
  return {
    title: { text: '故障停机帕累托图', left: 'center', top: 2, textStyle: { fontSize: 14 } },
    legend: {
      top: 20,
      data: ['停机时长', '累计占比'],
      textStyle: { color: '#606266' },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const idx = Number(params?.[0]?.dataIndex ?? -1)
        const meta = idx >= 0 ? source[idx]?.alarm_meta || {} : {}
        const p1 = params.find((x) => x.seriesName === '停机时长')
        const p2 = params.find((x) => x.seriesName === '累计占比')
        const a = p1 ? Number(p1.value || 0) : 0
        const b = p2 ? Number(p2.value || 0) : 0
        const title = params[0]?.axisValue || ''
        return `${title}<br/>故障码：${meta.alarm_code ?? '-'}<br/>停机时长：${a.toFixed(0)} s<br/>累计占比：${b.toFixed(2)}%`
      },
    },
    grid: { left: 52, right: 52, top: 40, bottom: 74 },
    xAxis: {
      type: 'category',
      data: labels,
      name: '报警类别',
      nameLocation: 'middle',
      nameGap: 60,
      axisLabel: {
        interval: 0,
        formatter: (value) => wrapAxisLabel(value, 6),
      },
    },
    yAxis: [
      { type: 'value', name: '停机时长（秒）' },
      {
        type: 'value',
        name: '累计占比（%）',
        min: 0,
        max: 100,
        axisLabel: { formatter: '{value}%' },
      },
    ],
    series: [
      { type: 'bar', name: '停机时长', data: bars, itemStyle: { color: '#409eff' } },
      { type: 'line', name: '累计占比', yAxisIndex: 1, data: cumulative, smooth: true, itemStyle: { color: '#f56c6c' } },
    ],
  }
}

function buildGanttOption() {
  const nowMs = Date.now()
  const windowStartMs = nowMs - Number(ganttWindowMinutes.value || DEFAULT_GANTT_WINDOW_MINUTES) * 60 * 1000
  const source = (diagPayload.value.events || []).filter((x) => {
    if (!x.start_time || !x.end_time) return false
    const start = new Date(x.start_time).getTime()
    const end = new Date(x.end_time).getTime()
    return end >= windowStartMs && start <= nowMs
  })
  const categories = Array.from(new Set(source.map((x) => x.equip_id)))
  const mappedDisplay = categories.map((equipId) => GANTT_EQUIP_DISPLAY_MAP[equipId] || equipId)
  const known = new Set(mappedDisplay)
  const extras = mappedDisplay.filter((name) => !GANTT_DISPLAY_ORDER.includes(name))
  const displayCategories = [
    ...GANTT_DISPLAY_ORDER.filter((name) => known.has(name)),
    ...extras,
  ]
  const safeDisplayCategories = displayCategories.length ? displayCategories : ['暂无设备']
  const catIndex = new Map(safeDisplayCategories.map((name, idx) => [name, idx]))
  const maxLabelLen = safeDisplayCategories.reduce((m, cur) => Math.max(m, String(cur || '').length), 0)
  const leftPadding = Math.min(300, Math.max(170, 36 + maxLabelLen * 9))
  const clipped = source
    .filter((x) => x.start_time && x.end_time)
    .map((x) => {
      const startMs = Math.max(new Date(x.start_time).getTime(), windowStartMs)
      const endMs = Math.min(new Date(x.end_time).getTime(), nowMs)
      const displayName = GANTT_EQUIP_DISPLAY_MAP[x.equip_id] || x.equip_id
      return {
        equip_id: x.equip_id,
        display_name: displayName,
        state: x.state_code || 'unknown',
        start_ms: startMs,
        end_ms: endMs,
      }
    })
    .filter((x) => x.end_ms > x.start_ms && catIndex.has(x.display_name))

  // 左侧补齐：若某设备在窗口起点之前已有连续状态，则将首段延展到窗口起点，避免大片空白。
  const byEquip = new Map()
  for (const seg of clipped) {
    if (!byEquip.has(seg.display_name)) byEquip.set(seg.display_name, [])
    byEquip.get(seg.display_name).push(seg)
  }
  const normalized = []
  for (const [displayName, arr] of byEquip.entries()) {
    arr.sort((a, b) => a.start_ms - b.start_ms)
    if (arr.length && arr[0].start_ms > windowStartMs) {
      arr.unshift({
        equip_id: arr[0].equip_id,
        display_name: displayName,
        state: arr[0].state,
        start_ms: windowStartMs,
        end_ms: arr[0].start_ms,
      })
    } else if (arr.length) {
      arr[0].start_ms = windowStartMs
    }
    normalized.push(...arr)
  }

  const data = normalized.map((x) => [
    catIndex.get(x.display_name),
    x.start_ms,
    x.end_ms,
    x.state,
    x.equip_id,
  ])
  const minTime = data.length ? windowStartMs : null
  const maxTime = data.length ? nowMs : null

  return {
    title: { text: '多设备状态甘特图', left: 'center', top: 2, textStyle: { fontSize: 14 } },
    graphic: data.length
      ? []
      : [
          {
            type: 'text',
            left: 'center',
            top: 'middle',
            style: {
              text: '当前筛选范围无状态事件',
              fill: '#909399',
              fontSize: 14,
            },
          },
        ],
    tooltip: {
      formatter: (params) => {
        const [idx, start, end, state] = params.value
        const equip = safeDisplayCategories[idx] || ''
        const rawEquip = params.value?.[4] || ''
        const stateLabel = DIAG_STATE_LABELS[state] || state
        const duration = Math.max(0, Math.round((Number(end) - Number(start)) / 1000))
        return `${equip}${rawEquip ? `（${rawEquip}）` : ''}<br/>状态：${stateLabel}<br/>区间：${formatLocalTime(new Date(start))} - ${formatLocalTime(new Date(end))}<br/>时长：${duration} s`
      },
    },
    grid: { left: leftPadding, right: 18, top: 44, bottom: 56 },
    xAxis: {
      type: 'time',
      min: minTime || undefined,
      max: maxTime || undefined,
      axisLabel: {
        formatter: (value) => echarts.time.format(value, '{HH}:{mm}:{ss}', false),
      },
    },
    yAxis: {
      type: 'category',
      data: safeDisplayCategories,
      name: '设备',
      axisLabel: {
        fontSize: 11,
        interval: 0,
      },
    },
    dataZoom: [{ type: 'inside', filterMode: 'none', xAxisIndex: 0 }],
    series: [
      {
        type: 'custom',
        coordinateSystem: 'cartesian2d',
        dimensions: ['equip_idx', 'start_ms', 'end_ms', 'state', 'equip_id'],
        encode: {
          x: [1, 2],
          y: 0,
          tooltip: [1, 2, 3, 4],
        },
        data,
        renderItem: (params, api) => {
          const categoryIndex = api.value(0)
          const start = api.coord([api.value(1), categoryIndex])
          const end = api.coord([api.value(2), categoryIndex])
          const height = api.size([0, 1])[1] * 0.58
          const state = api.value(3)
          const rect = echarts.graphic.clipRectByRect(
            {
              x: start[0],
              y: start[1] - height / 2,
              width: end[0] - start[0],
              height,
            },
            {
              x: params.coordSys.x,
              y: params.coordSys.y,
              width: params.coordSys.width,
              height: params.coordSys.height,
            }
          )
          return rect && {
            type: 'rect',
            shape: rect,
            style: {
              fill: GANTT_STATE_COLOR_MAP[state] || '#409eff',
            },
            info: { stateLabel: DIAG_STATE_LABELS[state] || state },
          }
        },
      },
    ],
  }
}

function renderDiagnosticCharts() {
  ensureDiagnosticCharts()
  if (paretoChart) paretoChart.setOption(buildParetoOption(), true)
  if (ganttChart) ganttChart.setOption(buildGanttOption(), true)
}

function handleResize() {
  radarChart?.resize()
  paretoChart?.resize()
  ganttChart?.resize()
}

async function fetchWorkshopKpi() {
  loading.value = true
  try {
    const { data } = await axios.get(KPI_TREE_API, { timeout: 3000 })
    if (!data?.ok || !data.workshop) throw new Error(data?.message || '接口返回异常')
    workshop.value = data.workshop
    targetDate.value = data.target_date || localDateText()
    lastRefreshAt.value = formatLocalTime()
    error.value = ''

    if (!selectedPayload.value) {
      selectedPayload.value = data.workshop
      selectedNodeKey.value = 'workshop'
    } else {
      syncSelectedPayload()
    }
    await nextTick()
    renderCharts()
  } catch (err) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function fetchDiagnostics() {
  const equipIds = selectedEquipIds.value
  if (!equipIds.length) {
    diagPayload.value = { events: [], alarm_top5: [], loss_breakdown: [] }
    diagError.value = ''
    return
  }
  diagLoading.value = true
  try {
    const { data } = await axios.get(EVENTS_API, {
      timeout: 5000,
      params: {
        limit: 2000,
        equip_ids: equipIds.join(','),
      },
    })
    if (!data?.ok) throw new Error(data?.message || '诊断接口返回异常')
    diagPayload.value = {
      events: data.events || [],
      alarm_top5: data.alarm_top5 || [],
      loss_breakdown: data.loss_breakdown || [],
    }
    diagError.value = ''
    await nextTick()
    renderDiagnosticCharts()
  } catch (err) {
    diagError.value = err?.message || '诊断数据加载失败'
  } finally {
    diagLoading.value = false
  }
}

function useSuggestedAgentQuestion() {
  agentQuestion.value = suggestedAgentQuestion.value
}

function clearAgentConversation() {
  agentConversationId.value = newConversationId()
  agentMessages.value = [
    {
      role: 'assistant',
      content: '对话已清空。设备诊断请写明完整设备 ID 和日期；通用诊断知识可以直接提问。',
      trace: [],
    },
  ]
  agentError.value = ''
}

async function submitAgentQuestion() {
  const question = agentQuestion.value.trim()
  if (!question || agentLoading.value) return
  agentMessages.value.push({ role: 'user', content: question, trace: [] })
  await scrollAgentToBottom()
  agentQuestion.value = ''
  agentLoading.value = true
  agentError.value = ''
  try {
    const { data } = await axios.post(
      AGENT_API,
      { question, conversation_id: agentConversationId.value },
      { timeout: 60000 }
    )
    const answer = data?.answer || data?.message || '本次诊断没有生成回答。'
    if (data?.conversation_id) agentConversationId.value = data.conversation_id
    agentMessages.value.push({
      role: 'assistant',
      content: answer,
      trace: data?.trace || [],
      status: data?.status || 'unknown',
      usage: data?.usage || null,
      modelRequests: data?.model_requests || 0,
      latencyMs: data?.latency_ms ?? null,
      grounding: data?.grounding || null,
      requestId: data?.request_id || '',
    })
    await scrollAgentToBottom()
  } catch (err) {
    const message = err?.response?.data?.message || err?.message || 'Agent 请求失败'
    agentError.value = message
    agentMessages.value.push({ role: 'assistant', content: `诊断失败：${message}`, trace: [], status: 'error' })
    await scrollAgentToBottom()
  } finally {
    agentLoading.value = false
  }
}

function syncSelectedPayload() {
  const ws = workshop.value
  if (!ws) return
  const key = selectedNodeKey.value
  if (key === 'workshop') {
    selectedPayload.value = ws
    return
  }
  const lineMatch = /^line-(\d+)$/.exec(key)
  if (lineMatch) {
    const line = (ws.lines || []).find((x) => x.line_index === Number(lineMatch[1]))
    selectedPayload.value = line || ws
    return
  }
  const stationMatch = /^line-(\d+)-station-(\d+)$/.exec(key)
  if (stationMatch) {
    const line = (ws.lines || []).find((x) => x.line_index === Number(stationMatch[1]))
    const station = (line?.stations || []).find((x) => x.station_no === Number(stationMatch[2]))
    selectedPayload.value = station || line || ws
  }
}

onMounted(async () => {
  await fetchWorkshopKpi()
  await nextTick()
  renderCharts()
  await fetchDiagnostics()
  pollTimer = window.setInterval(fetchWorkshopKpi, 5000)
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
  window.removeEventListener('resize', handleResize)
  radarChart?.dispose()
  paretoChart?.dispose()
  ganttChart?.dispose()
})

watch(
  () => [selectedNodeKey.value, selectedPayload.value],
  async () => {
    await nextTick()
    renderCharts()
    await fetchDiagnostics()
  },
  { deep: true }
)

watch(
  () => ganttWindowMinutes.value,
  async () => {
    await nextTick()
    renderDiagnosticCharts()
  }
)

</script>

<template>
  <div class="page">
    <div class="top-bar">
      <div>
        <div class="title">工业设备智能诊断平台</div>
        <div class="subtitle">实时 KPI · 可追溯诊断 · 工业知识库检索</div>
      </div>
      <div class="status">
        <el-tag type="warning" effect="plain">模拟数据</el-tag>
        <el-tag :type="error ? 'danger' : loading ? 'info' : 'success'">
          {{ error ? '连接异常' : loading && !workshop ? '连接中' : loading ? '更新中' : '已连接' }}
        </el-tag>
        <span class="refresh">数据日期：{{ targetDate || '--' }}</span>
        <span class="refresh">最近刷新：{{ lastRefreshAt || '--' }}</span>
      </div>
    </div>

    <el-card class="agent-panel" shadow="never">
      <template #header>
        <div class="agent-header">
          <div>
            <span class="agent-title">工业设备诊断 Agent</span>
            <el-tag class="agent-tag" size="small" type="success">DeepSeek + 7 Tools</el-tag>
            <el-tag class="agent-tag" size="small" type="info">诊断知识库 RAG</el-tag>
          </div>
          <el-button text :disabled="agentLoading" @click="clearAgentConversation">清空对话</el-button>
        </div>
      </template>

      <div class="agent-layout">
        <div ref="agentConversationRef" class="agent-conversation">
          <div
            v-for="(message, index) in agentMessages"
            :key="`agent-message-${index}`"
            class="agent-message"
            :class="message.role"
          >
            <div class="agent-role">{{ message.role === 'user' ? '你' : '诊断 Agent' }}</div>
            <div class="agent-bubble agent-markdown" v-html="renderAgentMarkdown(message.content)"></div>
            <el-collapse v-if="message.trace?.length" class="agent-trace">
              <el-collapse-item :title="`工具执行记录（${message.trace.length} 次）`">
                <div v-for="item in message.trace" :key="item.tool_call_id" class="trace-row">
                  <div>
                    <el-tag size="small">{{ item.name }}</el-tag>
                    <el-tag
                      class="trace-status"
                      size="small"
                      :type="item.result?.status === 'ok' ? 'success' : 'warning'"
                    >
                      {{ item.result?.status || 'unknown' }}
                    </el-tag>
                  </div>
                  <code>{{ JSON.stringify(item.arguments) }} · {{ item.latency_ms ?? 0 }} ms</code>
                </div>
              </el-collapse-item>
            </el-collapse>
            <div v-if="message.usage" class="agent-usage">
              模型请求 {{ message.modelRequests }} 次 · {{ message.usage.total_tokens || 0 }} tokens
              <template v-if="message.latencyMs !== null"> · {{ message.latencyMs }} ms</template>
              <template v-if="message.grounding?.status === 'verified'"> · RAG 引用已验证</template>
              <template v-else-if="message.grounding?.status === 'missing'"> · RAG 引用缺失</template>
              <template v-if="message.requestId"> · 请求 {{ message.requestId.slice(0, 8) }}</template>
            </div>
          </div>
          <div v-if="agentLoading" class="agent-waiting">
            正在选择工具并分析数据…
          </div>
        </div>

        <div class="agent-compose">
          <div class="agent-suggestion">
            <span>{{ suggestedAgentQuestion }}</span>
            <el-button
              size="small"
              @click="useSuggestedAgentQuestion"
            >
              使用此问题
            </el-button>
          </div>
          <el-input
            v-model="agentQuestion"
            type="textarea"
            :rows="3"
            maxlength="4000"
            show-word-limit
            placeholder="例如：查询 BJ-CNC-001 今日 OEE；或询问 OPC UA 数据不更新如何排查"
            @keydown.enter.exact.prevent="submitAgentQuestion"
          />
          <div class="agent-actions">
            <span class="agent-hint">Enter 发送，Shift+Enter 换行。同一会话保留最近 8 条消息，提问可能产生 API 费用。</span>
            <el-button
              type="primary"
              :loading="agentLoading"
              :disabled="!agentQuestion.trim()"
              @click="submitAgentQuestion"
            >
              开始诊断
            </el-button>
          </div>
          <el-alert
            v-if="agentError"
            class="agent-error"
            type="error"
            show-icon
            :closable="false"
            :title="agentError"
          />
        </div>
      </div>
    </el-card>

    <el-row :gutter="14">
      <el-col :span="6" class="sidebar-col">
        <el-card class="panel" shadow="never">
          <template #header>
            <span>功能树</span>
          </template>
          <el-tree
            :data="treeData"
            node-key="key"
            default-expand-all
            highlight-current
            :current-node-key="selectedNodeKey"
            @node-click="handleNodeClick"
          />
        </el-card>
      </el-col>

      <el-col :span="18" class="content-col">
        <el-card class="panel" shadow="never" v-loading="loading">
          <template #header>
            <span>{{ selectedPayload?.label || '指标详情' }}</span>
          </template>
          <el-alert
            v-if="kpiAlerts.length"
            class="warn-tip"
            type="warning"
            show-icon
            :closable="false"
            title="KPI 演示阈值预警"
            :description="kpiAlerts.join('；')"
          />
          <el-alert
            v-if="selectedDataWarnings.length"
            class="warn-tip"
            :type="selectedDataWarnings.includes('检测到状态事件重叠，当前指标不宜用于结论') ? 'error' : 'info'"
            show-icon
            :closable="false"
            title="数据质量说明"
            :description="selectedDataWarnings.join('；')"
          />

          <template v-if="currentNodeType === 'station'">
            <el-row :gutter="12" class="chart-block">
              <el-col v-if="showStationRadar" :span="12">
                <el-card class="metric-card" shadow="never">
                  <div ref="radarRef" class="chart h300"></div>
                </el-card>
              </el-col>
              <el-col :span="showStationRadar ? 12 : 24">
                <el-row :gutter="10">
                  <el-col v-for="card in percentMetricCards" :key="card.label" :span="12">
                    <el-card class="gauge-card" shadow="never">
                      <div class="metric-label">{{ card.label }}</div>
                      <el-progress
                        type="dashboard"
                        :percentage="Number(card.value.toFixed(2))"
                        :stroke-width="12"
                        :width="108"
                      />
                    </el-card>
                  </el-col>
                  <el-col v-for="card in stationNumericCards" :key="`num-${card.label}`" :span="12">
                    <el-card class="numeric-card gauge-card" shadow="never">
                      <div class="metric-label">{{ card.label }}</div>
                      <div class="numeric-value">{{ Number(card.value || 0).toFixed(2) }} min</div>
                    </el-card>
                  </el-col>
                </el-row>
              </el-col>
            </el-row>
            <el-descriptions title="时间要素（秒）" :column="4" border>
              <el-descriptions-item label="Tval">{{ selectedPayload?.time_factors_sec?.t_val ?? 0 }}</el-descriptions-item>
              <el-descriptions-item label="Tloss">{{ selectedPayload?.time_factors_sec?.t_loss ?? 0 }}</el-descriptions-item>
              <el-descriptions-item label="Tdown">{{ selectedPayload?.time_factors_sec?.t_down ?? 0 }}</el-descriptions-item>
              <el-descriptions-item label="Tplan">{{ selectedPayload?.time_factors_sec?.t_plan_down ?? 0 }}</el-descriptions-item>
              <el-descriptions-item label="Tnon_sch">{{ selectedPayload?.time_factors_sec?.t_non_sch ?? 0 }}</el-descriptions-item>
              <el-descriptions-item label="Top">{{ selectedPayload?.time_factors_sec?.t_op ?? 0 }}</el-descriptions-item>
              <el-descriptions-item label="Tload">{{ selectedPayload?.time_factors_sec?.t_load ?? 0 }}</el-descriptions-item>
              <el-descriptions-item label="Ttotal">{{ selectedPayload?.time_factors_sec?.t_total ?? 0 }}</el-descriptions-item>
            </el-descriptions>
          </template>

          <template v-else-if="currentNodeType === 'line'">
            <el-row :gutter="12" class="chart-block">
              <el-col :span="12">
                <el-card class="metric-card" shadow="never">
                  <div ref="radarRef" class="chart h300"></div>
                </el-card>
              </el-col>
              <el-col :span="12">
                <el-row :gutter="10">
                  <el-col v-for="card in percentMetricCards" :key="card.label" :span="12">
                    <el-card class="gauge-card" shadow="never">
                      <div class="metric-label">{{ card.label }}</div>
                      <el-progress
                        type="dashboard"
                        :percentage="Number(card.value.toFixed(2))"
                        :stroke-width="12"
                        :width="108"
                      />
                    </el-card>
                  </el-col>
                </el-row>
              </el-col>
            </el-row>
            <el-table :data="selectedPayload?.stations || []" style="margin-top: 12px" border>
              <el-table-column prop="label" label="工位" />
              <el-table-column prop="equip_id" label="设备ID" />
              <el-table-column label="OEE">
                <template #default="{ row }">{{ lineStationMetricText(row, 'oee') }}</template>
              </el-table-column>
              <el-table-column label="时间开动率">
                <template #default="{ row }">{{ lineStationMetricText(row, 'availability') }}</template>
              </el-table-column>
              <el-table-column label="性能开动率">
                <template #default="{ row }">{{ lineStationMetricText(row, 'performance') }}</template>
              </el-table-column>
              <el-table-column label="质量指数">
                <template #default="{ row }">{{ lineStationMetricText(row, 'quality') }}</template>
              </el-table-column>
              <el-table-column label="MTBF">
                <template #default="{ row }">{{ lineStationMetricText(row, 'mtbf') }}</template>
              </el-table-column>
              <el-table-column label="MTTR">
                <template #default="{ row }">{{ lineStationMetricText(row, 'mttr') }}</template>
              </el-table-column>
            </el-table>
          </template>

          <template v-else>
            <el-row :gutter="12" class="chart-block">
              <el-col :span="12">
                <el-card class="metric-card" shadow="never">
                  <div ref="radarRef" class="chart h300"></div>
                </el-card>
              </el-col>
              <el-col :span="12">
                <el-row :gutter="10">
                  <el-col v-for="card in percentMetricCards" :key="card.label" :span="12">
                    <el-card class="gauge-card" shadow="never">
                      <div class="metric-label">{{ card.label }}</div>
                      <el-progress
                        type="dashboard"
                        :percentage="Number(card.value.toFixed(2))"
                        :stroke-width="12"
                        :width="108"
                      />
                    </el-card>
                  </el-col>
                </el-row>
              </el-col>
            </el-row>
            <el-table :data="selectedPayload?.lines || []" style="margin-top: 16px" border>
              <el-table-column prop="label" label="产线" />
              <el-table-column label="OEE">
                <template #default="{ row }">{{ formatPercent(row.metrics?.oee) }}</template>
              </el-table-column>
              <el-table-column label="良品率">
                <template #default="{ row }">{{ formatPercent(row.metrics?.good_rate) }}</template>
              </el-table-column>
              <el-table-column label="设备维护率">
                <template #default="{ row }">{{ formatPercent(row.metrics?.maintenance_rate) }}</template>
              </el-table-column>
              <el-table-column label="无故障时间率">
                <template #default="{ row }">{{ formatPercent(row.metrics?.fault_free_rate) }}</template>
              </el-table-column>
              <el-table-column label="完好设备">
                <template #default="{ row }">{{ row.healthy_count }}/{{ row.total_devices }}</template>
              </el-table-column>
            </el-table>
          </template>

          <el-card class="metric-card analysis-card" shadow="never">
            <div class="analysis-title">规则分析与排查建议（模拟）</div>
            <el-row :gutter="12" class="analysis-split-row">
              <el-col :span="12">
                <div class="analysis-subtitle">分析</div>
                <ul class="analysis-list">
                  <li v-for="(item, idx) in monitorInsights" :key="`m-a-${idx}`">{{ item }}</li>
                </ul>
              </el-col>
              <el-col :span="12">
                <div class="analysis-subtitle">排查建议（待现场验证）</div>
                <ul class="analysis-list">
                  <li v-for="(item, idx) in monitorOptimizations" :key="`m-o-${idx}`">{{ item }}</li>
                </ul>
              </el-col>
            </el-row>
          </el-card>

          <el-divider />
          <el-row :gutter="12" v-loading="diagLoading">
            <el-col :span="24">
              <el-card class="metric-card" shadow="never">
                <div ref="paretoRef" class="chart diag-chart"></div>
              </el-card>
            </el-col>
            <el-col :span="24">
              <el-card class="metric-card analysis-card" shadow="never">
                <div class="analysis-title">故障停机帕累托规则分析（模拟）</div>
                <el-row :gutter="12" class="analysis-split-row">
                  <el-col :span="12">
                    <div class="analysis-subtitle">分析</div>
                    <ul class="analysis-list">
                      <li v-for="(item, idx) in paretoInsights" :key="`p-a-${idx}`">{{ item }}</li>
                    </ul>
                  </el-col>
                  <el-col :span="12">
                    <div class="analysis-subtitle">排查建议（待现场验证）</div>
                    <ul class="analysis-list">
                      <li v-for="(item, idx) in paretoOptimizations" :key="`p-o-${idx}`">{{ item }}</li>
                    </ul>
                  </el-col>
                </el-row>
              </el-card>
            </el-col>
            <el-col :span="24">
              <el-card class="metric-card" shadow="never">
                <div class="state-legend">
                  <span class="state-legend-title">颜色说明：</span>
                  <span v-for="item in GANTT_STATE_LEGEND" :key="item.key" class="state-legend-item">
                    <i class="state-legend-dot" :style="{ backgroundColor: GANTT_STATE_COLOR_MAP[item.key] }"></i>
                    <span>{{ item.label }}</span>
                  </span>
                </div>
                <div class="gantt-toolbar">
                  <span class="gantt-toolbar-label">时间范围：</span>
                  <el-radio-group v-model="ganttWindowMinutes" size="small">
                    <el-radio-button v-for="mins in GANTT_WINDOW_OPTIONS" :key="mins" :value="mins">
                      最近{{ mins }}分钟
                    </el-radio-button>
                  </el-radio-group>
                </div>
                <div ref="ganttRef" class="chart diag-chart"></div>
              </el-card>
            </el-col>
            <el-col :span="24">
              <el-card class="metric-card analysis-card" shadow="never">
                <div class="analysis-title">多设备状态甘特图规则分析（模拟）</div>
                <el-row :gutter="12" class="analysis-split-row">
                  <el-col :span="12">
                    <div class="analysis-subtitle">分析</div>
                    <ul class="analysis-list">
                      <li v-for="(item, idx) in ganttInsights" :key="`g-a-${idx}`">{{ item }}</li>
                    </ul>
                  </el-col>
                  <el-col :span="12">
                    <div class="analysis-subtitle">排查建议（待现场验证）</div>
                    <ul class="analysis-list">
                      <li v-for="(item, idx) in ganttOptimizations" :key="`g-o-${idx}`">{{ item }}</li>
                    </ul>
                  </el-col>
                </el-row>
              </el-card>
            </el-col>
          </el-row>
          <el-alert
            v-if="diagError"
            type="warning"
            show-icon
            :closable="false"
            :title="`诊断数据加载失败：${diagError}`"
          />
        </el-card>
      </el-col>
    </el-row>

    <el-alert
      v-if="error"
      class="error-tip"
      type="error"
      show-icon
      :closable="false"
      :title="`数据加载失败：${error}`"
    />
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding: 14px;
  background: #f5f7fa;
}

.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.title {
  font-size: 20px;
  font-weight: 700;
  color: #1f2d3d;
}

.subtitle {
  margin-top: 3px;
  color: #909399;
  font-size: 12px;
}

.status {
  display: flex;
  align-items: center;
  gap: 10px;
}

.refresh {
  color: #606266;
  font-size: 13px;
}

.panel {
  border-radius: 10px;
}

.agent-panel {
  margin-bottom: 14px;
  border-radius: 10px;
  border-color: #cfe3ff;
}

.agent-panel :deep(.el-card__header) {
  padding: 12px 16px;
  background: linear-gradient(90deg, #eef6ff 0%, #f8fbff 100%);
}

.agent-header,
.agent-actions,
.agent-suggestion {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.agent-title {
  font-size: 16px;
  font-weight: 700;
  color: #1f2d3d;
}

.agent-tag {
  margin-left: 10px;
}

.agent-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(340px, 0.8fr);
  gap: 16px;
}

.agent-conversation {
  min-height: 170px;
  max-height: 360px;
  overflow-y: auto;
  padding: 10px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #f8fafc;
}

.agent-message {
  margin-bottom: 12px;
}

.agent-message.user {
  text-align: right;
}

.agent-role {
  margin-bottom: 4px;
  color: #909399;
  font-size: 12px;
}

.agent-bubble {
  display: inline-block;
  max-width: 92%;
  padding: 9px 11px;
  border-radius: 8px;
  color: #303133;
  background: #fff;
  white-space: pre-wrap;
  text-align: left;
  line-height: 1.6;
  box-shadow: 0 1px 3px rgba(31, 45, 61, 0.08);
}

.agent-markdown :deep(p) {
  margin: 0 0 8px;
}

.agent-markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.agent-markdown :deep(h1),
.agent-markdown :deep(h2),
.agent-markdown :deep(h3) {
  margin: 10px 0 6px;
  font-size: 15px;
}

.agent-markdown :deep(ul),
.agent-markdown :deep(ol) {
  margin: 6px 0;
  padding-left: 20px;
}

.agent-markdown :deep(table) {
  width: 100%;
  margin: 8px 0;
  border-collapse: collapse;
  font-size: 12px;
}

.agent-markdown :deep(th),
.agent-markdown :deep(td) {
  padding: 5px 7px;
  border: 1px solid #dcdfe6;
}

.agent-message.user .agent-bubble {
  color: #fff;
  background: #409eff;
}

.agent-trace {
  margin-top: 6px;
  text-align: left;
}

.agent-trace :deep(.el-collapse-item__header) {
  height: 34px;
  color: #606266;
  background: transparent;
  font-size: 12px;
}

.trace-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px solid #ebeef5;
}

.trace-row code {
  color: #606266;
  font-size: 11px;
  overflow-wrap: anywhere;
}

.trace-status {
  margin-left: 5px;
}

.agent-usage,
.agent-hint,
.agent-waiting {
  margin-top: 5px;
  color: #909399;
  font-size: 12px;
}

.agent-compose {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.agent-suggestion {
  padding: 8px 10px;
  border-radius: 6px;
  color: #606266;
  background: #f0f7ff;
  font-size: 12px;
  line-height: 1.5;
}

.agent-error {
  margin-top: 2px;
}

@media (max-width: 960px) {
  .agent-layout {
    grid-template-columns: 1fr;
  }

  .agent-actions,
  .agent-suggestion {
    align-items: flex-start;
    flex-direction: column;
  }
}

@media (max-width: 760px) {
  .top-bar {
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 10px;
  }

  .status {
    flex-wrap: wrap;
  }

  .sidebar-col,
  .content-col {
    flex: 0 0 100%;
    max-width: 100%;
  }

  .sidebar-col {
    position: static;
    margin-bottom: 12px;
  }

  .sidebar-col :deep(.el-card__body) {
    max-height: none;
  }

  .chart-block > .el-col-12,
  .analysis-split-row > .el-col-12 {
    flex: 0 0 100%;
    max-width: 100%;
  }
}

.sidebar-col {
  position: sticky;
  top: 12px;
  align-self: flex-start;
}

.sidebar-col :deep(.el-card__body) {
  max-height: calc(100vh - 72px);
  overflow: auto;
}

.metric-card {
  margin-bottom: 12px;
}

.warn-tip {
  margin-bottom: 10px;
}

.gauge-card {
  margin-bottom: 10px;
  text-align: center;
}

.chart-block {
  margin-bottom: 4px;
}

.chart {
  width: 100%;
}

.h300 {
  height: 300px;
}

.diag-chart {
  /* 让诊断图按屏幕高度自适应，避免过长或过短 */
  height: clamp(220px, 28vh, 320px);
}

.state-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  margin: 0 0 6px;
  font-size: 12px;
  color: #606266;
}

.state-legend-title {
  color: #303133;
  font-weight: 600;
}

.state-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.state-legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  display: inline-block;
  border: 1px solid rgba(0, 0, 0, 0.08);
}

.gantt-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 2px 0 10px;
}

.gantt-toolbar-label {
  font-size: 12px;
  color: #606266;
}

.analysis-card {
  min-height: 150px;
  background: #fcfdff;
  border: 1px solid #e9edf5;
}

.analysis-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}

.analysis-split-row {
  margin-top: 2px;
}

.analysis-subtitle {
  font-size: 13px;
  font-weight: 600;
  color: #409eff;
  margin-bottom: 6px;
}

.analysis-list {
  margin: 0;
  padding-left: 18px;
  color: #606266;
  line-height: 1.7;
  font-size: 13px;
}

.h220 {
  height: 220px;
}

.metric-label {
  font-size: 13px;
  color: #606266;
}

.numeric-card {
  text-align: center;
  background: #fafcff;
  border: 1px solid #e6edf6;
}

.numeric-value {
  margin-top: 8px;
  font-size: 26px;
  font-weight: 700;
  color: #303133;
}

.error-tip {
  margin-top: 12px;
}
</style>

import { createApp } from 'vue'
import {
  ElAlert,
  ElButton,
  ElCard,
  ElCol,
  ElCollapse,
  ElCollapseItem,
  ElDescriptions,
  ElDescriptionsItem,
  ElDivider,
  ElInput,
  ElLoading,
  ElProgress,
  ElRadioButton,
  ElRadioGroup,
  ElRow,
  ElTable,
  ElTableColumn,
  ElTag,
  ElTree,
} from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'

const app = createApp(App)

for (const component of [
  ElAlert, ElButton, ElCard, ElCol, ElCollapse, ElCollapseItem,
  ElDescriptions, ElDescriptionsItem, ElDivider, ElInput, ElProgress,
  ElRadioButton, ElRadioGroup, ElRow, ElTable, ElTableColumn, ElTag, ElTree,
]) {
  app.use(component)
}
app.use(ElLoading)

app.mount('#app')

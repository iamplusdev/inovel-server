<script setup lang="ts">
/**
 * 维护分组壳：书库体检与书架备份恢复。
 * “书架同步”原名与实际行为不符（它做的是备份恢复），改名为“书架备份”。
 */
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import GroupTabs from "@/components/GroupTabs.vue";

const route = useRoute();
const router = useRouter();

const tabs = [
  { name: "check", label: "体检" },
  { name: "shelf-restore", label: "书架备份" },
];

const active = computed(() => String(route.name ?? "check"));

function go(name: string) {
  if (name === route.name) return;
  void router.push({ name });
}
</script>

<template>
  <div class="page group-page">
    <header class="page-header">
      <div>
        <h1 class="page-title">维护</h1>
        <p class="page-subtitle">书库体检与 Legado 书架备份恢复</p>
      </div>
    </header>

    <GroupTabs :items="tabs" :active="active" @select="go" />

    <router-view v-slot="{ Component }">
      <component :is="Component" embedded />
    </router-view>
  </div>
</template>

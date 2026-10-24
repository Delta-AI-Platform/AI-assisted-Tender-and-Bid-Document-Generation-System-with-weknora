package agent

import (
	"strings"
	"testing"
)

func TestBidGuardRejectsCompletionAndKeepsDownloads(t *testing.T) {
	got := guardedBidAnswer("完整投标文件候选稿已制作完成。资格全部满足。\n[草稿](resource://file)", "待核证")
	if strings.Contains(got, "资格全部满足") || !strings.Contains(got, "resource://file") || !strings.Contains(got, "待核证") {
		t.Fatal(got)
	}
}
func TestBidGuardSuppressesToolProtocol(t *testing.T) {
	got := guardedBidAnswer("<tool_call>shell_exec<arg_key>command</arg_key>", "未完成")
	if strings.Contains(got, "<tool_call>") || !strings.Contains(got, "协议异常") {
		t.Fatal(got)
	}
}

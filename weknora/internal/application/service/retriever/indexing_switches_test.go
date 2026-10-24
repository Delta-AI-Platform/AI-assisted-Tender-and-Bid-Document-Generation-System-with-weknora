package retriever

import (
	"context"
	"github.com/Tencent/WeKnora/internal/models/embedding"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/stretchr/testify/require"
	"testing"
)

type captureIndexSwitches struct {
	mockEngineService
	received []types.RetrieverType
}

func (s *captureIndexSwitches) BatchIndex(_ context.Context, _ embedding.Embedder, _ []*types.IndexInfo, k []types.RetrieverType) error {
	s.received = append([]types.RetrieverType(nil), k...)
	return nil
}
func TestKnowledgeBaseIndexingSwitchesPreserveSharedEngine(t *testing.T) {
	for _, tc := range []struct {
		vector, keyword bool
		want            []types.RetrieverType
	}{
		{false, true, []types.RetrieverType{types.KeywordsRetrieverType}},
		{true, false, []types.RetrieverType{types.VectorRetrieverType}},
		{true, true, []types.RetrieverType{types.VectorRetrieverType, types.KeywordsRetrieverType}},
	} {
		service := &captureIndexSwitches{}
		original := []types.RetrieverType{types.VectorRetrieverType, types.KeywordsRetrieverType}
		shared := &CompositeRetrieveEngine{engineInfos: []*engineInfo{{retrieveEngine: service, retrieverType: original}}}
		scoped := shared.WithIndexingTypes(tc.vector, tc.keyword)
		require.NoError(t, scoped.BatchIndex(context.Background(), nil, []*types.IndexInfo{{SourceID: "source"}}))
		require.Equal(t, tc.want, service.received)
		require.Equal(t, original, shared.engineInfos[0].retrieverType)
	}
}

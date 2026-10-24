package router

import (
	"github.com/gin-gonic/gin"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
)

// Evaluation-only adapter: fixed local service; never accepts a caller URL.
// The delivery service rechecks ownership using the forwarded bearer token.
func bidDelivery(c *gin.Context) {
	if os.Getenv("DETA_BID_DELIVERY_ENABLED") != "true" {
		c.Status(http.StatusNotFound)
		return
	}
	target, _ := url.Parse("http://127.0.0.1:8767")
	proxy := httputil.NewSingleHostReverseProxy(target)
	director := proxy.Director
	proxy.Director = func(r *http.Request) {
		director(r)
		r.URL.Path = "/sessions/" + url.PathEscape(c.Param("id")) + "/artifacts"
		if id := c.Param("artifact_id"); id != "" {
			r.URL.Path += "/" + url.PathEscape(id) + "/download"
		}
		if c.FullPath() == "/api/v1/sessions/:id/bid-libraries" {
            r.URL.Path = "/sessions/" + url.PathEscape(c.Param("id")) + "/libraries"
        } else { r.URL.RawQuery = "" }
		r.Header.Del("Cookie")
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		http.Error(w, "File delivery unavailable", http.StatusBadGateway)
	}
	proxy.ServeHTTP(c.Writer, c.Request)
}

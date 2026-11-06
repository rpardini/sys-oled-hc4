{{- define "sys-oled.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sys-oled.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "sys-oled.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "sys-oled.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "sys-oled.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sys-oled.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "sys-oled.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "sys-oled.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "sys-oled.nodeSelector" -}}
{{- if .Values.nodeSelector -}}
{{- toYaml .Values.nodeSelector -}}
{{- else -}}
{{ printf "%s/enabled" .Values.nodeConfig.prefix }}: "true"
{{- end -}}
{{- end -}}

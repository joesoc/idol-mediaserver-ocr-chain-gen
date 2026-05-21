<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform">

    <xsl:output method="xml" indent="yes" encoding="UTF-8" omit-xml-declaration="yes"/>

    <!-- XSLT 1.0 helper: recursively strips path segments to return the filename -->
    <xsl:template name="last-path-segment">
        <xsl:param name="path"/>
        <xsl:choose>
            <xsl:when test="contains($path, '/')">
                <xsl:call-template name="last-path-segment">
                    <xsl:with-param name="path" select="substring-after($path, '/')"/>
                </xsl:call-template>
            </xsl:when>
            <xsl:otherwise>
                <xsl:value-of select="$path"/>
            </xsl:otherwise>
        </xsl:choose>
    </xsl:template>

    <xsl:template match="/">
        <adds>
            <add>
                <document>

                    <!-- Unique Reference -->
                    <reference>
                        <xsl:value-of select="/output/metadata/segment/uuid"/>
                    </reference>

                    <!-- Title -->
                    <DRETITLE>
                        <xsl:value-of select="(//ObjectRecognitionResult)[1]/identity/identifier"/>
                        <xsl:text> </xsl:text>
                        <xsl:choose>
                            <xsl:when test="//record[trackname='OCR_ACT_DL_Number.Result']/OCRResult/text">
                                <xsl:value-of select="//record[trackname='OCR_ACT_DL_Number.Result']/OCRResult/text"/>
                            </xsl:when>
                            <xsl:when test="//record[trackname='OCR_NSW_DL_Number.Result']/OCRResult/text">
                                <xsl:value-of select="//record[trackname='OCR_NSW_DL_Number.Result']/OCRResult/text"/>
                            </xsl:when>
                            <xsl:when test="//record[trackname='OCR_VIC_DL_Number.Result']/OCRResult/text">
                                <xsl:value-of select="//record[trackname='OCR_VIC_DL_Number.Result']/OCRResult/text"/>
                            </xsl:when>
                            <xsl:otherwise>
                                <xsl:value-of select="//record[trackname='OCR_WA_DL_Licence_Number.Result']/OCRResult/text"/>
                            </xsl:otherwise>
                        </xsl:choose>
                    </DRETITLE>

                    <!-- Source Information -->
                    <SOURCE>
                        <xsl:value-of select="/output/metadata/session/source"/>
                    </SOURCE>
                    <FILENAME>
                        <xsl:call-template name="last-path-segment">
                            <xsl:with-param name="path" select="/output/metadata/session/source"/>
                        </xsl:call-template>
                    </FILENAME>

                    <!-- Driver Licence Details -->
                    <LICENSE_TYPE>
                        <xsl:value-of select="(//ObjectRecognitionResult)[1]/identity/identifier"/>
                    </LICENSE_TYPE>
                    
                    <LICENSE_CONFIDENCE>
                        <xsl:value-of select="(//ObjectRecognitionResult)[1]/identity/confidence"/>
                    </LICENSE_CONFIDENCE>

                    <!-- ACT Driver Licence (only shown when LICENSE_TYPE is ACT_DL) -->
                    <xsl:if test="(//ObjectRecognitionResult)[1]/identity/identifier = 'ACT_DL'">
                        <ACT_DL_NUMBER>
                            <xsl:value-of select="//record[trackname='OCR_ACT_DL_Number.Result']/OCRResult/text"/>
                        </ACT_DL_NUMBER>
                        <ACT_CONFIDENCE>
                            <xsl:value-of select="//record[trackname='OCR_ACT_DL_Number.Result']/OCRResult/confidence"/>
                        </ACT_CONFIDENCE>
                    </xsl:if>

                    <!-- NSW Driver Licence (only shown when LICENSE_TYPE is NSW_DL) -->
                    <xsl:if test="(//ObjectRecognitionResult)[1]/identity/identifier = 'NSW_DL'">
                        <NSW_DL_NUMBER>
                            <xsl:value-of select="//record[trackname='OCR_NSW_DL_Number.Result']/OCRResult/text"/>
                        </NSW_DL_NUMBER>
                        <NSW_CONFIDENCE>
                            <xsl:value-of select="//record[trackname='OCR_NSW_DL_Number.Result']/OCRResult/confidence"/>
                        </NSW_CONFIDENCE>
                    </xsl:if>

                    <!-- QLD Driver Licence (only shown when LICENSE_TYPE is QLD_DL) -->
                    <xsl:if test="(//ObjectRecognitionResult)[1]/identity/identifier = 'QLD_DL'">
                        <QLD_DL_NUMBER>
                            <xsl:value-of select="//record[trackname='OCR_QLD_DL_Number.Result']/OCRResult/text"/>
                        </QLD_DL_NUMBER>
                        <QLD_CONFIDENCE>
                            <xsl:value-of select="//record[trackname='OCR_QLD_DL_Number.Result']/OCRResult/confidence"/>
                        </QLD_CONFIDENCE>
                    </xsl:if>

                    <!-- TAS Driver Licence (only shown when LICENSE_TYPE is TAS_DL) -->
                    <xsl:if test="(//ObjectRecognitionResult)[1]/identity/identifier = 'TAS_DL'">
                        <TAS_DL_NUMBER>
                            <xsl:value-of select="//record[trackname='OCR_TAS_DL_Number.Result']/OCRResult/text"/>
                        </TAS_DL_NUMBER>
                        <TAS_CONFIDENCE>
                            <xsl:value-of select="//record[trackname='OCR_TAS_DL_Number.Result']/OCRResult/confidence"/>
                        </TAS_CONFIDENCE>
                    </xsl:if>

                    <!-- VIC_DL Driver Licence -->
                    <xsl:if test="(//ObjectRecognitionResult)[1]/identity/identifier = 'VIC_DL'">
                        <VIC_DL_NUMBER>
                            <xsl:value-of select="//record[trackname='OCR_VIC_DL_Number.Result']/OCRResult/text"/>
                        </VIC_DL_NUMBER>
                        <VIC_DL_CONFIDENCE>
                            <xsl:value-of select="//record[trackname='OCR_VIC_DL_Number.Result']/OCRResult/confidence"/>
                        </VIC_DL_CONFIDENCE>
                    </xsl:if>

                    <!-- WA_DL Driver Licence -->
                    <xsl:if test="(//ObjectRecognitionResult)[1]/identity/identifier = 'WA_DL'">
                        <WA_DL_LICENCE_NUMBER>
                            <xsl:value-of select="//record[trackname='OCR_WA_DL_Licence_Number.Result']/OCRResult/text"/>
                        </WA_DL_LICENCE_NUMBER>
                        <WA_DL_CONFIDENCE>
                            <xsl:value-of select="//record[trackname='OCR_WA_DL_Licence_Number.Result']/OCRResult/confidence"/>
                        </WA_DL_CONFIDENCE>
                    </xsl:if>

                    <!-- Metadata -->
                    <DREDATE>
                        <xsl:value-of select="format-number(/output/metadata/segment/currentTime/utcMicroSeconds div 1000000, '#')"/>
                    </DREDATE>

                    <CONTENT_TYPE>driver_licence</CONTENT_TYPE>

                </document>
            </add>
        </adds>
    </xsl:template>

</xsl:stylesheet>
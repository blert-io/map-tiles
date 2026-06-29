import groovy.json.JsonSlurper
import java.net.URL

plugins {
    id("java")
    id("com.gradleup.shadow") version "9.0.0-beta13"
    id("io.freefair.lombok") version "8.13.1"
}

group = "org.explv"
version = "1.0"

repositories {
    mavenCentral()

    maven {
        url = uri("https://repo.runelite.net")
        content {
            includeGroup("net.runelite")
        }
    }
}

var latestRl: String? = null
fun getLatestRunelite(): String {
    if (latestRl != null) {
        return latestRl!!
    }

    val jsonText = URL("https://static.runelite.net/bootstrap.json").readText()
    val json = JsonSlurper().parseText(jsonText) as Map<String, String>
    latestRl = json["version"]
    return latestRl!!
}

dependencies {
    implementation("net.runelite:cache:${getLatestRunelite()}")
    implementation("commons-cli:commons-cli:1.3.1")
    implementation("org.antlr:antlr4-runtime:4.13.1")
    implementation("org.slf4j:slf4j-api:1.7.25")
}

tasks.shadowJar {
    manifest {
        attributes["Main-Class"] = "org.explv.mapimage.Main"
    }
}

tasks.build {
    dependsOn(tasks.shadowJar)
}
